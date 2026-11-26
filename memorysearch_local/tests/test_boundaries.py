"""No model and no private-library access. Run with the system Python."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase,main
from unittest.mock import patch
import json,os,sys

REPO=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(REPO))
from memorysearch_local.config import load,private_write,LocalError,storage_gate
from memorysearch_local.discovery import walk_media
from memorysearch_local.photo_review_runtime import projection
from memorysearch_local.runtime_compat import contract_test
from memorysearch_local import control

class BoundaryTests(TestCase):
 def test_discovery_excludes_index_aliases_and_hidden_files(self):
  with TemporaryDirectory() as directory:
   root=Path(directory);index=root/'index';index.mkdir();(index/'thumb.jpg').touch()
   (root/'source.jpg').touch();(root/'index.json').write_text('{}');(root/'.hidden.jpg').touch()
   (root/'alias.jpg').symlink_to(root/'source.jpg')
   self.assertEqual([rel for _,rel in walk_media(root,index)],['source.jpg'])
 def test_projection_preserves_members_and_order_with_unknown(self):
  rows=[{'asset':{'id':str(i)}} for i in range(10)]
  value=projection(rows,{'3':'PASS','5':'UNKNOWN','6':'FAIL'})
  self.assertEqual(value['confirmed'][0]['asset']['id'],'3')
  self.assertEqual(value['original_results'],rows)
  self.assertEqual({r['asset']['id'] for r in value['results']},{str(i) for i in range(10)})
  self.assertEqual(len(value['remaining']),9)
  self.assertEqual(projection(rows,fallback='timeout')['results'],rows)
 def test_projection_rejects_duplicate_or_extra_members(self):
  with self.assertRaises(ValueError):projection([{'asset':{'id':'same'}}]*2)
  with self.assertRaises(ValueError):projection([{'asset':{'id':str(i)}} for i in range(11)])
 def test_sync_unknown_and_unsafe_metadata_block(self):
  cfg=SimpleNamespace(synthetic=False,no_sync_confirmed=False)
  with self.assertRaises(LocalError):storage_gate(cfg,{'checks':[]})
  cfg.no_sync_confirmed=True
  with self.assertRaises(LocalError):storage_gate(cfg,{'checks':[{'icloud_targeted':True}]})
 def test_synthetic_scope_cannot_relabel_an_arbitrary_root(self):
  with TemporaryDirectory() as directory:
   root=Path(directory);media=root/'media';media.mkdir();(media/'SYNTHETIC-ONLY').touch()
   path=root/'config.json';private_write(path,{'schema':1,'SCOPE':'synthetic','MEDIA_ROOT':str(media),'INDEX_ROOT':str(media/'.memorysearch-index'),'RUNTIME_PYTHON':'runtime/python/bin/python3','ENCODER_MODEL_ROOT':'runtime/models/embeddinggemma-2','VERIFIER_MODEL_ROOT':'runtime/models/verifier-4b','NATIVE_BIN_ROOT':'runtime/native'})
   with self.assertRaises(LocalError):load(path)
 def test_start_does_not_duplicate_an_initializing_owned_worker(self):
  with TemporaryDirectory() as directory:
   state=Path(directory);private_write(state/'owner.json',{'pid':123,'instance_id':'nonce'})
   cfg=SimpleNamespace(state=state)
   status={'pid':123,'instance_id':'nonce','phase':'network_proof'}
   with patch.object(control,'health',return_value=status),patch.object(control,'alive',return_value=True),patch.object(control,'_start_locked') as launch:
    self.assertEqual(control.start(cfg)['phase'],'network_proof');launch.assert_not_called()
 def test_transformers_feature_signature(self):self.assertTrue(contract_test()['passed'])

if __name__=='__main__':main()
