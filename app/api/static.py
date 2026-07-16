"""Static serving of the built single-page frontend.

The API lives under ``/api``; everything else is the built Vue app. Two
behaviours a plain ``StaticFiles`` mount lacks:

* **SPA fallback** — unknown non-API paths return ``index.html`` so client-side
  routes and installed-PWA launches deep-link correctly.
* **Update-safe caching** — ``index.html``, the service worker, and the
  manifest are served ``no-cache`` so deployed updates propagate on the next
  launch; Vite's hashed assets keep long-lived caching by content address.
"""

from __future__ import annotations

from pathlib import Path

from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

# Filenames whose content changes in place between deploys (everything else in
# a Vite build is content-hashed). Served no-cache so browsers and the service
# worker revalidate them and pick up new releases.
_NO_CACHE_FILES = frozenset({"index.html", "sw.js", "manifest.webmanifest", "registerSW.js"})


class SPAStaticFiles(StaticFiles):
    """``StaticFiles`` with an ``index.html`` fallback for client-side routes."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        """Serve the requested file, falling back to ``index.html`` on 404.

        Args:
            path: URL path relative to the mount.
            scope: The ASGI scope of the request.

        Returns:
            The file response, or the SPA shell for unknown non-API paths.

        Raises:
            HTTPException: Re-raised errors other than 404, and 404s for
                ``api/*`` paths (an unknown API route must stay a JSON 404,
                never become HTML).
        """
        # Starlette normalizes the path with os.path.normpath, so on Windows
        # segments are backslash-separated; normalize before inspecting.
        segments = path.replace("\\", "/").strip("/").split("/")
        try:
            response = await super().get_response(path, scope)
        except HTTPException as error:
            if error.status_code != 404 or segments[0] == "api":
                raise
            response = await super().get_response("index.html", scope)
        served = getattr(response, "path", "")
        if served and Path(served).name in _NO_CACHE_FILES:
            response.headers["Cache-Control"] = "no-cache"
        return response
