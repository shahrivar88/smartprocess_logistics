#!/bin/sh
# Remove one half-made site directory from the isolation lab's own volume, and nothing else.
#
# Why this exists as a script rather than a line typed into a shell: an earlier `new-site` was
# interrupted, leaving `sites/sp_logistics_lab` behind with a `site_config.json` and no database.
# `bench new-site` refuses a directory that is already there, so the directory has to go before the
# site can be created — and "the directory has to go" is exactly the kind of sentence that ends in
# an `rm -rf` that was not aimed carefully. Every guard below either passes or this script exits
# without touching anything.
#
# Run it inside a container that has the lab's sites volume mounted:
#
#   docker run --rm -e SP_LAB_CONFIRM=sp_logistics_lab -v sp_logistics_lab_sites:/sites \
#     -v "<this repo>/deployment/lab:/lab:ro" alpine sh /lab/cleanup_incomplete_site.sh sp_logistics_lab
#
# The database side is checked by the person running it, from the lab's own database container:
# this script cannot see MariaDB, so it cannot prove that no database exists — only the command in
# `deployment/lab/README.md` can, and it is step zero of the same procedure.

set -eu

SITES=/sites
ALLOWED=sp_logistics_lab
site="${1:-}"

fail() {
	echo "refused: $1" >&2
	exit 1
}

[ "$site" = "$ALLOWED" ] || fail "this script removes only '$ALLOWED', and was asked for '${site:-nothing}'"
[ "${SP_LAB_CONFIRM:-}" = "$site" ] || fail "set SP_LAB_CONFIRM=$site to confirm the target"

target="$SITES/$site"

[ -d "$target" ] || fail "$target does not exist"
[ -f "$target/site_config.json" ] || fail "$target is not a site directory (no site_config.json)"

# A half-made site has no data of any kind. If any of these are present, this is not the case this
# script was written for, and a person has to look before anything is removed.
for suspicious in \
	"$target/private/backups" \
	"$target/private/files" \
	"$target/private/encryption_key" \
	"$target/.backups"
do
	if [ -e "$suspicious" ]; then
		entries=$(find "$suspicious" -mindepth 1 2>/dev/null | wc -l)

		[ "$entries" = "0" ] || fail "$suspicious holds $entries entries; this site may have data"
	fi
done

# Nothing outside the target directory may be mentioned. A guard against a glob that grew.
parent_entries=$(ls -1 "$SITES" | wc -l)

echo "about to remove: $target"
echo "siblings in $SITES: $parent_entries entries, none of which will be touched"
du -sh "$target" 2>/dev/null || true

rm -rf -- "$target"

[ -e "$target" ] && fail "the directory is still there" || true

echo "removed: $target"
echo "left in $SITES:"
ls -1 "$SITES"
