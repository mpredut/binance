# Historical tools

This directory replaces the old generic `altele/` directory. It holds experiments,
converters and maintenance operations that are only ever launched by hand.

Some scripts can reach real APIs or modify files in `cachedb/`. For the maintenance ones,
use the dry-run mode first where it exists, and stop the writer processes as the script
instructs.
