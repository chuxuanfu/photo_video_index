"""Request-scoped file limits; vector matrices cache the complete index, never a page."""
from contextvars import ContextVar
FILE_LIMIT=ContextVar('memorysearch_file_limit',default=10)
MAX_RESULTS=50
def validate_limit(value):
 if not isinstance(value,int) or isinstance(value,bool) or not 1<=value<=MAX_RESULTS:raise ValueError('result_limit')
 return value
def rank_files(rank,query,matrix,budget,exclude_assets=None,only_assets=None):
 total=len(matrix.asset_ids);need=min(FILE_LIMIT.get(),len(set(matrix.asset_ids)))
 budget=min(budget,total)
 while True:
  hits=rank(query,matrix,budget,exclude_assets,only_assets)
  if len({hit.asset_id for hit in hits})>=need or budget>=total:return hits
  budget=min(total,max(budget+1,budget*2))
