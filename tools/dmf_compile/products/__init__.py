"""One module per product type (decision D4). Unknown types are refused by compile rule 1."""
from . import dmf_mxl, switchframe

MODULES = [switchframe, dmf_mxl]
TYPES = {t: m for m in MODULES for t in m.TYPES}
