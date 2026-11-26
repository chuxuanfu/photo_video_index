"""Preserve the upstream feature-method signature for Transformers 5.x routing."""
from functools import wraps
import inspect
def install_counter(base):
 module=base.model.model
 original=type(module).get_image_features.__get__(module,type(module))
 @wraps(original)
 def counted(*args,**kwargs):
  base.visual_calls+=1
  return original(*args,**kwargs)
 module.get_image_features=counted
 assert inspect.signature(counted)==inspect.signature(original)
 return True
def contract_test():
 from types import SimpleNamespace
 class Feature:
  def get_image_features(self,pixel_values,image_grid_thw=None,return_dict=False):
   return (pixel_values,image_grid_thw,return_dict)
 f=Feature();original=f.get_image_features
 f.get_image_features=lambda *args,**kwargs:original(*args,**kwargs)
 old=set(inspect.signature(f.get_image_features).parameters)
 assert 'pixel_values' not in old
 base=SimpleNamespace(model=SimpleNamespace(model=f),visual_calls=0)
 install_counter(base)
 new=set(inspect.signature(f.get_image_features).parameters)
 assert 'pixel_values' in new and 'image_grid_thw' in new
 assert f.get_image_features(7,image_grid_thw=9,return_dict=True)==(7,9,True)
 assert base.visual_calls==1
 return dict(old_wrapper_missing_pixel_signature=True,fixed_signature_preserved=True,
    exact_return_parity=True,counter_increment=True,passed=True)
