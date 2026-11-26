from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from memorysearch_local.search_limits import validate_limit,rank_files,FILE_LIMIT
from memorysearch_local.photo_review_runtime import projection

class ResultLimitTests(TestCase):
 def test_rejects_coercion_and_out_of_range(self):
  for value in [True,False,0,51,-1,2.5,'25',None]:
   with self.assertRaises(ValueError):validate_limit(value)
  for value in [1,5,10,20,25,50]:self.assertEqual(validate_limit(value),value)
 def test_file_budget_reaches_beyond_many_windows_of_one_video(self):
  ids=['long']*900+['video-'+str(n) for n in range(60)]
  matrix=SimpleNamespace(asset_ids=ids);calls=[]
  def rank(q,m,n,*args):calls.append(n);return [SimpleNamespace(asset_id=x) for x in m.asset_ids[:n]]
  token=FILE_LIMIT.set(50)
  try:hits=rank_files(rank,None,matrix,600)
  finally:FILE_LIMIT.reset(token)
  self.assertGreater(len(set(h.asset_id for h in hits)),49);self.assertGreater(calls[-1],600)
 def test_grouping_preserves_all_25_and_never_confirms_video(self):
  rows=[{'asset':{'id':str(n),'media_type':'video' if n%3==0 else 'image'}} for n in range(25)]
  value=projection(rows,{str(n):'PASS' for n in range(25)},limit=25)
  self.assertEqual(len(value['results']),25);self.assertEqual(value['original_results'],rows)
  self.assertTrue(all(r['asset']['media_type']=='image' for r in value['confirmed']))
  self.assertEqual(projection(rows,limit=25,fallback='timeout')['results'],rows)
