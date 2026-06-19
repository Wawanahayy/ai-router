#!/usr/bin/env python3
import uvicorn
import os, sys
import logging
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ai_router import config

_root = logging.getLogger()
if not any(isinstance(h, logging.StreamHandler) for h in _root.handlers):
    _h = logging.StreamHandler(sys.stderr)
    _h.setLevel(logging.INFO)
    _h.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    _root.addHandler(_h)
for _name in ("ai_router", "ai_router.proxy", "ai_router.services.translator", "ai_router.services.streaming"):
    logging.getLogger(_name).setLevel(logging.INFO)

if __name__ == "__main__":
    uvicorn.run(
        "ai_router.server:app",
        host=config.HOST,
        port=config.PORT,
        reload=False,
        log_level="info"
    )
