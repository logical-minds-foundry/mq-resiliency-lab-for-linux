# shellcheck shell=bash
# lab/boxes/_box-register.sh - SOURCED (not executed) by the box builders.
#
# Keep a REUSE box registered instead of re-adding it on every run (#1248).
#
# Before #1248 the REUSE path of build-fatbox.sh / rhel/build-box.sh ran
# `vagrant box add --force` from the cache on EVERY run, even when the same cache
# was already registered. That cost the unpack (obs alone took 65-72s) and, worse,
# gave the registered box.img a new mtime each time. vagrant-libvirt names a box's
# base volume `<box>_vagrant_box_image_0_<mtime of box.img>_box.img` for an
# unversioned box (vagrant-libvirt 0.12.2, action/handle_box_image.rb,
# get_volume_name), so each re-add made the next `vagrant up` upload the whole
# base image into the libvirt pool again (about 10 GiB per macOS run).
#
# The fix records WHICH cache artifact a registration came from, in a stamp file
# beside the registered box.img. The REUSE path skips the add only when every
# registered copy carries the identity of the cache it would add now. The identity
# is the cache path + size + mtime + manifest hash: a rebake (BUILD, `box rebuild`)
# rewrites the cache, so its mtime changes and the box is re-added. `box clean`
# and `vagrant box remove` delete the box directory, stamp included, and a VM
# rebuild wipes it with the rest of the boot disk.
#
# A registration with NO stamp (added before #1248, or by hand) has an unknown
# source. It is adopted, stamp written and no add, only when its box.img and
# metadata.json are byte-identical to the cache's (a one-time full read of the
# cache). Anything else is re-added. Adopting rather than re-adding keeps the
# box.img mtime, and so keeps the libvirt base volume that already matches it.

# The stamp file's name, written beside each registered box.img.
BOX_REG_STAMP=mqlab-registration

# box_reg_provider_dirs <box> - the registered box.img directories for <box>, one
# per line (vagrant lays a box out as <name>/<version>/[<arch>/]libvirt/box.img;
# a '/' in the name is escaped as -VAGRANTSLASH-). Prints nothing when unregistered.
box_reg_provider_dirs() {
  local dir
  dir="${VAGRANT_HOME:-$HOME/.vagrant.d}/boxes/${1//\//-VAGRANTSLASH-}"
  [ -d "$dir" ] || return 0
  find "$dir" -name box.img -path '*/libvirt/*' -printf '%h\n' | sort
}

# box_reg_identity <cache> <manifest-hash|-> - the identity of a cache artifact.
box_reg_identity() {
  printf 'cache=%s size=%s mtime=%s manifest=%s\n' \
    "$1" "$(stat -c %s "$1")" "$(stat -c %Y "$1")" "$2"
}

# box_reg_state <box> <identity> - one word for the registration of <box>:
#   current   - every registered copy was added from exactly <identity>
#   stale     - a registered copy is stamped with a different identity
#   unstamped - registered, with no stamp on some copy (source unknown)
#   absent    - not registered
# Cheap: it reads only the stamp files, so `--dry-run` can print it.
box_reg_state() {
  local dirs d state=current
  dirs="$(box_reg_provider_dirs "$1")"
  if [ -z "$dirs" ]; then
    echo absent
    return 0
  fi
  while IFS= read -r d; do
    if [ ! -f "$d/$BOX_REG_STAMP" ]; then
      state=unstamped
    elif [ "$(cat "$d/$BOX_REG_STAMP")" != "$2" ]; then
      echo stale
      return 0
    fi
  done <<<"$dirs"
  echo "$state"
}

# box_reg_stamp <box> <identity> - stamp every registered copy of <box>. Fails loud
# when nothing is registered.
box_reg_stamp() {
  local dirs d
  dirs="$(box_reg_provider_dirs "$1")"
  if [ -z "$dirs" ]; then
    echo "ERROR: box '$1' has no registered box.img to stamp" >&2
    return 1
  fi
  while IFS= read -r d; do
    printf '%s\n' "$2" >"$d/$BOX_REG_STAMP"
  done <<<"$dirs"
}

# box_reg_same_as_cache <box> <cache> - 0 iff every registered copy's box.img and
# metadata.json are byte-identical to the members of the same name in <cache>.
# Reads the whole cache once per copy; used only to adopt an unstamped registration.
box_reg_same_as_cache() {
  local dirs d
  dirs="$(box_reg_provider_dirs "$1")"
  [ -n "$dirs" ] || return 1
  while IFS= read -r d; do
    cmp -s <(tar -xOf "$2" metadata.json) "$d/metadata.json" || return 1
    cmp -s <(tar -xOf "$2" box.img) "$d/box.img" || return 1
  done <<<"$dirs"
}

# box_register <box> <identity> <vagrant-box-add-args...> - add the box, then stamp
# every registered copy with <identity>. Fails loud when the add leaves no box.img.
box_register() {
  local box="$1" identity="$2"
  shift 2
  vagrant box add "$@"
  box_reg_stamp "$box" "$identity"
}

# box_reuse_register <box> <cache> <identity> <vagrant-box-add-args...> - the REUSE
# path: keep a current registration as-is, adopt an unstamped one that is identical
# to the cache, else (re-)add the box from the cache.
box_reuse_register() {
  local box="$1" cache="$2" identity="$3" state
  shift 3
  state="$(box_reg_state "$box" "$identity")"
  if [ "$state" = current ]; then
    echo "registration: current (added from this cache) - no vagrant box add needed"
    return 0
  fi
  if [ "$state" = unstamped ]; then
    echo "registration: unstamped - comparing the registered box with the cache..."
    if box_reg_same_as_cache "$box" "$cache"; then
      box_reg_stamp "$box" "$identity"
      echo "registration: adopted (identical to this cache) - no vagrant box add needed"
      return 0
    fi
    echo "registration: differs from this cache"
  fi
  echo "registration: ${state} - adding the box from the cache"
  box_register "$box" "$identity" "$@"
}
