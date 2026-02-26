"""
Django management command to import position snapshots from JSON file to database.

Usage:
    python manage.py import_positions
    
    # Import from specific file
    python manage.py import_positions --file position_snapshot.json
    
    # Import all positions (not just changes)
    python manage.py import_positions --all
    
    # Import including flat positions
    python manage.py import_positions --include-flat
"""
import os
from django.core.management.base import BaseCommand, CommandError
from smbweb.apps.executions.snapshot_db import save_snapshot_from_file


class Command(BaseCommand):
    help = 'Import position snapshots from JSON file into the positions table'

    def add_arguments(self, parser):
        parser.add_argument(
            '--file',
            type=str,
            default='position_snapshot.json',
            help='Path to the position snapshot JSON file (default: position_snapshot.json)'
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help='Import all positions, not just those with changes (WARNING: high data volume!)'
        )
        parser.add_argument(
            '--include-flat',
            action='store_true',
            help='Include flat positions (net_side=flat and total_magnitude=0)'
        )

    def handle(self, *args, **options):
        snapshot_file = options['file']
        save_only_changes = not options['all']
        save_flat_positions = options['include_flat']

        # Validate file exists
        if not os.path.exists(snapshot_file):
            raise CommandError(f'File not found: {snapshot_file}')

        self.stdout.write(
            self.style.SUCCESS(f'Reading position snapshot from: {snapshot_file}')
        )
        
        if save_only_changes:
            self.stdout.write(
                self.style.WARNING('Mode: Only importing positions with changes (delta_magnitude != 0 or change_type != null)')
            )
        else:
            self.stdout.write(
                self.style.WARNING('Mode: Importing ALL positions (high data volume!)')
            )
        
        if not save_flat_positions:
            self.stdout.write('Excluding flat positions (net_side=flat and total_magnitude=0)')
        else:
            self.stdout.write('Including flat positions')

        # Import data
        try:
            result = save_snapshot_from_file(
                snapshot_file=snapshot_file,
                save_only_changes=save_only_changes,
                save_flat_positions=save_flat_positions
            )

            # Display results
            self.stdout.write('\n' + '=' * 60)
            if result.get('error_message'):
                self.stdout.write(
                    self.style.ERROR(f'Error: {result["error_message"]}')
                )
            elif not result.get('has_changes', True) and save_only_changes:
                self.stdout.write(
                    self.style.WARNING(
                        'No changes detected in snapshot - nothing to import.\n'
                        'This is normal if positions haven\'t changed since last import.\n'
                        'Use --all to import all positions regardless of changes.'
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Import complete!\n'
                        f'  Saved: {result["saved"]}\n'
                        f'  Skipped: {result["skipped"]}\n'
                        f'  Errors: {result["errors"]}'
                    )
                )
            
            if result.get('errors', 0) > 0:
                self.stdout.write(
                    self.style.WARNING(
                        f'\n⚠️  {result["errors"]} error(s) occurred during import. '
                        'Check the output above for details.'
                    )
                )

        except Exception as e:
            raise CommandError(f'Error importing positions: {str(e)}')
