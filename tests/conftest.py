"""Shared configuration for the offline unit suite.

Tests here are pure logic: no network, no external services. Environment-driven
behaviour is exercised by monkeypatching settings, never by reading a
developer's real environment.
"""

from __future__ import annotations
