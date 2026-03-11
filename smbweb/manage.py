#!/usr/bin/env python
"""Django's command-line utility for administrative tasks.
Use it from the project root with:

python smbweb/manage.py runserver
python smbweb/manage.py migrate
python smbweb/manage.py import_positions
"""
import os
import sys
from pathlib import Path

if __name__ == '__main__':
    p_project_root = Path(__file__).resolve().parent.parent
    if str(p_project_root) not in sys.path:
        sys.path.insert(0, str(p_project_root))

    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smbweb.settings')
    os.environ['DJANGO_SERVER'] = '1'  # used for django java check

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?") from exc

    execute_from_command_line(sys.argv)
