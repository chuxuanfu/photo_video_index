"""Offline speech validation. Unreliable ASR is never treated as observed speech."""
from __future__ import annotations
import difflib
import json
from pathlib import Path
import re
import unicodedata
import wave
import os
import sys
import media_index as app


def canonical(text):
    text=unicodedata.normalize('NFKC',text).casefold().replace('’',"'")
    chars=[]
    for i,c in enumerate(text):
        before=text[i-1] if i else '';after=text[i+1] if i+1<len(text) else ''
        if c.isalnum() or (c in '.,٫٬' and before.isdigit() and after.isdigit()) or (c=="'" and before.isalpha() and after.isalpha()):chars.append(c)
        else:chars.append(' ')
    result=re.sub(r'\s+',' ',''.join(chars)).strip()
    return re.sub(r'(?<=[\u3400-\u9fff\u3040-\u30ff])\s+(?=[\u3400-\u9fff\u3040-\u30ff])','',result)


def texts_agree(left,right):
    key=canonical(left)
    return bool(key) and key==canonical(right)


def activity(audio):
    import webrtcvad
    detector=webrtcvad.Vad(2)
    with wave.open(str(audio),'rb') as wav:
        if wav.getframerate()!=16000 or wav.getnchannels()!=1 or wav.getsampwidth()!=2:
            raise app.IndexErrorUser('Speech validation needs 16kHz mono 16-bit PCM')
        duration=round(wav.getnframes()/16)
        flags=[]
        while True:
            frame=wav.readframes(480)
            if len(frame)!=960:break
            flags.append(detector.is_speech(frame,16000))
    return flags,duration


def reasons(row,flags):
    text=canonical(str(row.get('text','')))
    start=max(0,round(float(row['start'])*1000));end=round(float(row['end'])*1000)
    sample=flags[start//30:min(len(flags),(end+29)//30)]
    voiced=sum(sample)*30
    found=[]
    if not text:found.append('empty')
    if voiced<180 or (sample and sum(sample)/len(sample)<0.2):found.append('insufficient_speech_activity')
    if row.get('no_speech_prob',0)>0.6:found.append('no_speech_probability')
    if row.get('avg_logprob',0)<-1.0:found.append('low_log_probability')
    if row.get('compression_ratio',0)>2.4:found.append('repetition_compression')
    if any(float(w.get('probability',1))<0.35 for w in row.get('words',[]) or []):found.append('low_word_probability')
    if len(text)>=24 and (len(set(text))/len(text)<0.12 or re.search(r'(.)\1{14}',text)):
        found.append('repeated_characters')
    return found


def transcribe(audio,destination,args):
    import mlx_whisper
    os.environ["PATH"]=str(Path(sys.executable).parent)+os.pathsep+os.environ.get("PATH","")
    audio=Path(audio);destination=Path(destination)
    flags,duration=activity(audio)
    base=Path(__file__).resolve().parent/'.models'
    primary=base/'whisper-large-v3-turbo'
    reference=base/'whisper-small-mlx'
    if not primary.is_dir() or not reference.is_dir():
        raise app.IndexErrorUser('Both pinned local speech models must be installed; no remote fallback')
    common={'verbose':False,'temperature':0.0,'condition_on_previous_text':False,
            'compression_ratio_threshold':2.4,'logprob_threshold':-1.0,'no_speech_threshold':0.6}
    raw=mlx_whisper.transcribe(str(audio),path_or_hf_repo=str(primary),word_timestamps=True,
                               hallucination_silence_threshold=2.0,**common)
    second=mlx_whisper.transcribe(str(audio),path_or_hf_repo=str(reference),word_timestamps=True,**common)
    primary_rows=raw.get('segments',[]);reference_rows=second.get('segments',[])
    good_reference=[row for row in reference_rows if not reasons(row,flags)]
    whole_agrees=bool(primary_rows and reference_rows) and all(not reasons(row,flags) for row in [*primary_rows,*reference_rows]) and texts_agree(' '.join(row['text'] for row in primary_rows),' '.join(row['text'] for row in reference_rows))
    accepted=[];audit=[];review_required=0
    for row in raw.get('segments',[]):
        rejected=reasons(row,flags)
        peers=[p for p in good_reference if float(p['start'])<float(row['end'])+1 and float(p['end'])>float(row['start'])-1]
        peer_text=canonical(' '.join(str(p['text']) for p in peers))
        text=canonical(str(row['text']))
        agreement=difflib.SequenceMatcher(None,text,peer_text,autojunk=False).ratio()
        # Similar-looking strings may disagree on a color, negation or number.
        # Fuzzy similarity is diagnostic only; accepted text must agree exactly
        # after the documented punctuation/whitespace normalization.
        if text and not (whole_agrees or text==peer_text):
            rejected.append('independent_transcription_disagreement')
        if rejected:
            # A strong speech-like region with uncertain words needs review;
            # rejected repetition over noise does not become a transcript.
            segment=flags[max(0,round(float(row['start'])*1000)//30):min(len(flags),round(float(row['end'])*1000)//30)]
            if segment and sum(segment)/len(segment)>=0.5 and not any(r in rejected for r in ('repetition_compression','repeated_characters','no_speech_probability')):
                review_required+=1
        else:
            start=max(0,min(duration,round(float(row['start'])*1000)))
            end=max(0,min(duration,round(float(row['end'])*1000)))
            if end>start:accepted.append({'start_ms':start,'end_ms':end,'text':str(row['text']).strip()})
        audit.append({'start':row['start'],'end':row['end'],'text':row['text'],'reasons':rejected,
                      'agreement':agreement,'no_speech_prob':row.get('no_speech_prob'),'avg_logprob':row.get('avg_logprob'),'compression_ratio':row.get('compression_ratio')})
    if good_reference and not texts_agree(' '.join(r['text'] for r in primary_rows),' '.join(r['text'] for r in reference_rows)) and flags and sum(flags)/len(flags)>=0.2:
        review_required=max(1,review_required)
    if accepted and any(r['reasons'] for r in audit):review_required=max(1,review_required)
    accepted=app.suppress_silent_segments(audio,accepted,args.silence_threshold_db)
    app.save_transcript(destination,accepted)
    (destination/'speech-quality-audit.json').write_text(json.dumps({'primary':raw,'reference':second,'decisions':audit},ensure_ascii=False,indent=2)+'\n')
    return {'transcript':accepted,'audio_analysis':{
        'status':'needs_review' if review_required else 'reliable_transcript' if accepted else 'no_reliable_speech',
        'primary_model':'whisper-large-v3-turbo','reference_model':'whisper-small-mlx',
        'speech_activity_fraction':round(sum(flags)/len(flags),3) if flags else 0,
        'rejected_segments':sum(bool(r['reasons']) for r in audit),'uncertain_speech_segments':review_required,
        'verification':'local VAD, word/segment confidence and exact normalized transcription agreement; no human listening review',
        'coverage':'accepted spans only; not an exhaustive transcript',
        'limitation':'A missing transcript does not establish that no speech occurred.'}}
