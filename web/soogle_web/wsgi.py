"""
WSGI config for soogle_web project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os
import sys
from pathlib import Path

from django.core.wsgi import get_wsgi_application

# The repo root (parent of web/) holds the scrape package (shared search index).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'soogle_web.settings')

application = get_wsgi_application()
