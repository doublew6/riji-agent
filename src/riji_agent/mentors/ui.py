"""Static local discussion view; tokens stay in browser memory and HTTP headers."""

import base64
import hashlib
import re
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse


def build_review_router() -> APIRouter:
    router = APIRouter(include_in_schema=False)
    page = (Path(__file__).parent / "review.html").read_text()
    script = re.search(r"<script>(.*?)</script>", page, re.DOTALL).group(1)
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()

    @router.get("/admin/mentors")
    def review():
        return HTMLResponse(page, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; "
            "style-src 'unsafe-inline'; script-src 'sha256-" + digest + "'; connect-src 'self'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'none'"})

    return router
