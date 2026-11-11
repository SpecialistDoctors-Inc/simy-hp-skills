"""Packaged HP entrypoint. Customers do not install a Python interpreter."""
import json
import sys

import hp_local_editor
import hp_preview
import hp_transfer
from hp_codex_client import desktop_codex, TransportError


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else '--help'
    if command == 'doctor':
        try:
            executable = desktop_codex()
        except TransportError as error:
            print(json.dumps({'ready': False, 'error': str(error)}))
            return 1
        print(json.dumps({'ready': True, 'desktop_runtime': executable,
                          'bundled_python': bool(getattr(sys, 'frozen', False))}))
        return 0
    if command == 'preview':
        sys.argv.pop(1)
        hp_preview.main()
    elif command in ('download', 'upload'):
        hp_transfer.main()
    elif command in ('init', 'status', 'pack', 'accept'):
        hp_local_editor.main()
    else:
        print('HP local editor: doctor | download | init | status | pack | upload | accept | preview')
        return 0 if command in ('--help', '-h') else 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
