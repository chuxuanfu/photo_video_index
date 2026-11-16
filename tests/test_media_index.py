import argparse
import array
import io
import json
import shutil
import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import media_index as app


def options(**changes):
    values = dict(
        model=app.MODEL, ollama_url=app.OLLAMA_URL, timeout=30,
        limit=0, checkpoint_every=25, frame_interval=30.0, max_frames=0, asr="skip",
        asr_model=app.ASR_MODEL, whisper_model=None,
        silence_threshold_db=-55.0,
    )
    values.update(changes)
    return argparse.Namespace(**values)


def details(name="蓝色冲浪板"):
    return {
        "summary": f"海边有{name}。",
        "description": f"阴天的海边，画面中央有一块{name}，旁边的人穿白色短袖，右侧有浪花。",
        "objects": [name, "白色短袖的人"],
        "spatial_relations": ["冲浪板位于人物右侧"],
        "tags": ["海边", name], "ocr_text": "", "uncertainty_note": "人物身份无法确认",
    }


class IndexTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def test_isolated_trial_preserves_original_index_and_library_id(self):
        root = self.base / "OriginalPhoto"
        root.mkdir()
        (root / "sample.jpg").write_bytes(b"synthetic")
        with mock.patch.object(app, "describe_image", return_value=details()):
            app.index_root(str(root), "photo", options())
            original = (root / "index.json").read_bytes()
            output = self.base / "Trial"
            args = options(output_dir=str(output), model_backend="mlx-vlm", model_revision="fixed")
            app.index_root(str(root), "photo", args)
            first = app.read_index(output / "Photo/index.json")
            app.index_root(str(root), "photo", args)
        self.assertEqual((root / "index.json").read_bytes(), original)
        self.assertEqual(first["library_id"], json.loads(original)["library_id"])
        self.assertEqual(first["generation"], app.read_index(output / "Photo/index.json")["generation"])
        hits, _ = app.load_hits([str(output / "Photo/index.json")])
        self.assertEqual(app.locate(hits[0], {first["library_id"]: root}), (root / "sample.jpg").resolve())
        with self.assertRaises(app.IndexErrorUser):
            app.index_root(str(root), "photo", options(output_dir=str(root / "unsafe")))

    def test_model_url_refuses_external_destinations_and_redirects(self):
        for value in ["https://127.0.0.1:8080/v1", "http://example.com:8080/v1", "http://127.0.0.1.evil:8080", "http://user@127.0.0.1:8080", "http://127.0.0.1:8080/?x=1", "http://127.0.0.1:99999"]:
            with self.assertRaises(app.IndexErrorUser):
                app.local_model_url(value)
        self.assertEqual(app.local_model_url(app.MLX_VLM_URL), app.MLX_VLM_URL)
        with self.assertRaises(app.IndexErrorUser):
            app._NoModelRedirect().redirect_request(None, None, 307, "redirect", {}, "https://example.com")

    def test_mlx_vision_payload_and_schema_failure(self):
        args = options(model_backend="mlx-vlm", mlx_vlm_url=app.MLX_VLM_URL)
        response = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(details())}}]}
        with mock.patch.object(app, "local_model_open", return_value=io.BytesIO(json.dumps(response).encode())) as request:
            result = app.ollama_request([{"role": "user", "content": "synthetic", "images": ["YWJj"]}], app.VISION_SCHEMA, args)
        self.assertEqual(result["objects"], details()["objects"])
        sent = json.loads(request.call_args.args[0].data)
        self.assertEqual(sent["messages"][0]["content"][1]["image_url"]["url"], "data:image/jpeg;base64,YWJj")
        response["choices"][0]["message"]["content"] = '{"summary":"invalid"}'
        with mock.patch.object(app, "local_model_open", return_value=io.BytesIO(json.dumps(response).encode())):
            with self.assertRaises(app.IndexErrorUser):
                app.ollama_request([{"role": "user", "content": "synthetic"}], app.VISION_SCHEMA, args)

    def test_photo_relative_location_incremental_and_copy(self):
        root = self.base / "Photo"
        nested = root / "2024"
        nested.mkdir(parents=True)
        original = nested / "海边.jpg"
        original.write_bytes(b"fake jpg for mocked model")
        with mock.patch.object(app, "describe_image", return_value=details()):
            app.index_root(str(root), "photo", options())
            first = app.read_index(root / "index.json")
            app.index_root(str(root), "photo", options())
            second = app.read_index(root / "index.json")
        self.assertEqual(first["generation"], second["generation"])
        self.assertEqual(second["assets"][0]["relative_path"], "2024/海边.jpg")
        self.assertEqual(app.resolve_media((root / "index.json").parent, "2024/海边.jpg", "photo"), original.resolve())
        hits, _ = app.load_hits([str(root / "index.json")])
        result = app.search_hits(hits, "蓝色冲浪板", 10)[0]
        self.assertEqual(result.relative_path, "2024/海边.jpg")
        copied = self.base / "Copies" / "index.json"
        copied.parent.mkdir()
        shutil.copy2(root / "index.json", copied)
        copied_hits, _ = app.load_hits([str(copied)])
        self.assertIsNone(app.locate(copied_hits[0], {}))
        self.assertEqual(app.locate(copied_hits[0], {first["library_id"]: root}), original.resolve())

    def test_config_defaults_and_command_line_override(self):
        config_path = self.base / "config.json"
        config_path.write_text('{"schema_version":1,"vision_model":"qwen3.8:27b","frame_interval_seconds":15,"faster_whisper_model":"medium","semantic_search":true}', encoding="utf-8")
        config = app.load_config(config_path)
        args = app.parser(config).parse_args(["index", "--photo-root", "Photo", "--frame-interval", "30"])
        self.assertEqual(args.model, "qwen3.8:27b")
        self.assertEqual(args.frame_interval, 30)
        self.assertEqual(args.faster_whisper_model, "medium")
        with self.assertRaises(app.IndexErrorUser):
            (self.base / "missing.json").write_text('{"unknown_setting": 1}', encoding="utf-8")
            app.load_config(self.base / "missing.json")

    def test_auto_asr_selects_portable_backend(self):
        args = options(asr="auto")
        with mock.patch.object(app.sys, "platform", "linux"), mock.patch.object(app.shutil, "which", return_value=None):
            self.assertEqual(app.select_asr_backend(args), "faster")
        with mock.patch.object(app.sys, "platform", "darwin"), mock.patch.object(app.platform, "machine", return_value="arm64"), \
             mock.patch.object(app.importlib.util, "find_spec", return_value=object()), mock.patch.object(app.shutil, "which", return_value=None):
            self.assertEqual(app.select_asr_backend(args), "mlx")

    def test_faster_whisper_transcript_keeps_timestamps(self):
        audio = self.base / "speech.wav"
        samples = array.array("h", [8000] * 16000)
        with wave.open(str(audio), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(samples.tobytes())
        resources = self.base / "Resources"
        resources.mkdir()
        segment = types.SimpleNamespace(start=0.1, end=0.9, text="我们到了")
        model = mock.Mock()
        model.transcribe.return_value = ([segment], None)
        fake_module = types.SimpleNamespace(WhisperModel=mock.Mock(return_value=model))
        with mock.patch.dict(sys.modules, {"faster_whisper": fake_module}):
            rows = app.transcribe_audio(audio, resources, options(asr="faster", faster_whisper_model="small"))
        self.assertEqual(rows, [{"start_ms": 100, "end_ms": 900, "text": "我们到了"}])
        self.assertIn("我们到了", (resources / "transcript.srt").read_text(encoding="utf-8"))

    def test_invalid_paths_and_symlink_escape(self):
        root = self.base / "Photo"
        root.mkdir()
        for bad in ("/tmp/x.jpg", "../x.jpg", "a//b.jpg", "a/./b.jpg", "C:/x.jpg", "https://x/a.jpg"):
            with self.assertRaises(app.IndexErrorUser):
                app.validate_relative_path(bad, "photo")
        outside = self.base / "outside.jpg"
        outside.write_bytes(b"private")
        (root / "escape.jpg").symlink_to(outside)
        with self.assertRaises(app.IndexErrorUser):
            app.resolve_media(root, "escape.jpg", "photo")

    def test_duplicate_file_gets_new_id_but_move_keeps_id(self):
        root = self.base / "Photo"
        root.mkdir()
        first = root / "first.jpg"
        first.write_bytes(b"same content")
        with mock.patch.object(app, "describe_image", return_value=details()):
            app.index_root(str(root), "photo", options())
            old_id = app.read_index(root / "index.json")["assets"][0]["id"]
            shutil.copy2(first, root / "copy.jpg")
            app.index_root(str(root), "photo", options())
            ids = {a["relative_path"]: a["id"] for a in app.read_index(root / "index.json")["assets"]}
            self.assertNotEqual(ids["copy.jpg"], ids["first.jpg"])
            first.rename(root / "moved.jpg")
            app.index_root(str(root), "photo", options())
            moved_ids = {a["relative_path"]: a["id"] for a in app.read_index(root / "index.json")["assets"]}
        self.assertEqual(moved_ids["moved.jpg"], old_id)

    def test_video_resource_layout_and_timestamps(self):
        root = self.base / "Video"
        nested = root / "Event"
        nested.mkdir(parents=True)
        original = nested / "clip.mp4"
        original.write_bytes(b"fake video for mocked ffmpeg")

        def make_frame(_binary, _source, _at_ms, output):
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"frame")

        def make_audio(_binary, _source, output):
            output.write_bytes(b"audio")

        with mock.patch.object(app, "ffmpeg_path", return_value="ffmpeg"), \
             mock.patch.object(app, "video_info", return_value=(70000, True)), \
             mock.patch.object(app, "extract_frame", side_effect=make_frame), \
             mock.patch.object(app, "extract_audio", side_effect=make_audio), \
             mock.patch.object(app, "describe_image", return_value=details("红色箱子")), \
             mock.patch.object(app, "transcribe_audio", return_value=[{"start_ms": 28600, "end_ms": 33400, "text": "我们到了"}]), \
             mock.patch.object(app, "video_summary", return_value="站台视频显示红色箱子在人物旁，约三十秒音轨说我们到了。"):
            app.index_root(str(root), "video", options())
        data = app.read_index(root / "index.json")
        asset = data["assets"][0]
        self.assertEqual(asset["relative_path"], "Event/clip.mp4")
        self.assertEqual([frame["at_ms"] for frame in asset["frames"]], [0, 30000, 60000])
        self.assertTrue((root / "Resources" / "Event" / "clip.mp4" / "frames" / "000001.jpg").is_file())
        self.assertEqual(asset["transcript"][0]["start_ms"], 28600)
        self.assertTrue(any("我们到了" in item["speech_text"] for item in asset["moments"]))
        hits, _ = app.load_hits([str(root / "index.json")])
        result = app.search_hits(hits, "我们到了", 10)
        self.assertTrue(any(hit.at_ms in (28600, 30000) for hit in result))

    def test_near_silent_audio_does_not_become_searchable_speech(self):
        audio = self.base / "audio.wav"
        samples = array.array("h", [8000] * 16000 + [0] * 16000)
        with wave.open(str(audio), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(samples.tobytes())
        rows = [
            {"start_ms": 0, "end_ms": 900, "text": "真实对白"},
            {"start_ms": 1100, "end_ms": 1900, "text": "静音幻觉"},
        ]
        filtered = app.suppress_silent_segments(audio, rows, -55)
        self.assertEqual([row["text"] for row in filtered], ["真实对白"])

    def test_partial_frames_do_not_attach_late_speech_to_early_frame(self):
        frames = [{"at_ms": at, "description": "站台画面", "summary": "站台"} for at in (0, 30000, 60000)]
        transcript = [{"start_ms": 600000, "end_ms": 602000, "text": "十分钟后说话"}]
        moments = app.make_moments(frames, transcript, 900000, truncated_samples=True, frame_interval_ms=30000)
        self.assertEqual(moments[-1]["end_ms"], 75000)
        self.assertFalse(any(moment["speech_text"] for moment in moments))

    def test_specific_video_hit_keeps_its_timestamp(self):
        index = self.base / "Video" / "index.json"
        hits = [
            app.Hit(index, "video-1", "video", "clip.mp4", "video", "红色行李箱。我们到了。", 0),
            app.Hit(index, "video-1", "video", "clip.mp4", "frame", "30 秒抽帧：红色行李箱在车门旁", 30000),
            app.Hit(index, "video-1", "video", "clip.mp4", "speech", "我们到了。", 28600),
            app.Hit(index, "video-1", "video", "clip.mp4", "moment", "红色行李箱；我们到了。", 30000),
        ]
        self.assertEqual(app.search_hits(hits, "我们到了", 3)[0].at_ms, 28600)
        self.assertEqual(app.search_hits(hits, "红色行李箱", 3)[0].at_ms, 30000)

    def test_skip_asr_clears_stale_transcript_resources(self):
        resources = self.base / "Resources" / "clip.mp4"
        resources.mkdir(parents=True)
        (resources / "transcript.json").write_text('[{"text":"old"}]')
        (resources / "transcript.srt").write_text("old")
        self.assertEqual(app.transcribe_audio(self.base / "unused.wav", resources, options(asr="skip")), [])
        self.assertEqual((resources / "transcript.json").read_text().strip(), "[]")
        self.assertEqual((resources / "transcript.srt").read_text(), "")

    def test_resources_symlink_cannot_redirect_writes(self):
        root = self.base / "Video"
        outside = self.base / "outside"
        root.mkdir()
        outside.mkdir()
        (root / "Resources").symlink_to(outside)
        with self.assertRaises(app.IndexErrorUser):
            app.resource_directory(root, ("clip.mp4",))
        self.assertEqual(list(outside.iterdir()), [])

    def test_failed_file_keeps_prior_success_in_index(self):
        root = self.base / "Photo"
        root.mkdir()
        (root / "a.jpg").write_bytes(b"a")
        (root / "b.jpg").write_bytes(b"b")
        with mock.patch.object(app, "describe_image", side_effect=[details(), app.IndexErrorUser("model timeout")]):
            with self.assertRaises(app.IndexErrorUser):
                app.index_root(str(root), "photo", options())
        partial = app.read_index(root / "index.json")
        self.assertTrue(partial["scan_incomplete"])
        self.assertEqual([x["relative_path"] for x in partial["assets"]], ["a.jpg"])
        self.assertEqual(partial["failed_files"][0]["relative_path"], "b.jpg")
        with mock.patch.object(app, "describe_image", return_value=details()):
            app.index_root(str(root), "photo", options())
        complete = app.read_index(root / "index.json")
        self.assertFalse(complete["scan_incomplete"])
        self.assertEqual([x["relative_path"] for x in complete["assets"]], ["a.jpg", "b.jpg"])


if __name__ == "__main__":
    unittest.main()
