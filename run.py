#!/usr/bin/env python3
import uvicorn
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ai_router import config

if __name__ == "__main__":
    uvicorn.run(
        "ai_router.server:app",
        host=config.HOST,
        port=config.PORT,
        reload=False,
        log_level="info"
    )
