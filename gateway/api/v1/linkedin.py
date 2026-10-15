"""Vercel function for POST /v1/linkedin (see vercel.json for the rewrite)."""
import os
import sys

# The project root (gateway/) holds the `artie_gateway` package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from artie_gateway.adapter import GatewayHandler  # noqa: E402


class handler(GatewayHandler):  # noqa: N801 - the name Vercel's Python runtime looks for
    pass
