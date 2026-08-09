#!/bin/bash
# Builds a synthetic project tree for capturing README/social screenshots,
# so screenshots never expose real project names, plugin names, or paths.
#
# Usage:
#   docs/media/build_demo_data.sh [target-dir]
#   CUSTODIAN_PROJECT_SCAN_ROOT=<target-dir> python3 -m custodian.cli report
#
# Pair with CUSTODIAN_PROJECT_SCAN_ROOT (see custodian/discovery.py) to scope
# project discovery to just this synthetic tree -- engine discovery is
# deliberately left unscoped so real, installed engines still show up.
set -euo pipefail
DEMO="${1:-$HOME/CustodianDemo}"
rm -rf "$DEMO"
mkdir -p "$DEMO"

# name | cpp(1/0) | intermediate_MB | ddc_MB | binaries_MB | age_days | already_clean(1/0)
PROJECTS=(
  "SciFiCorridor|1|2400|1800|900|62|0"
  "RacingPrototype|0|1100|400|0|48|0"
  "ArchVizWalkthrough|1|0|0|0|90|1"
  "MetaHumanShowcase|0|2800|1200|0|33|0"
  "VRTrainingSim|1|650|180|420|71|0"
  "PuzzleAdventure|0|140|60|0|22|0"
  "HorrorEscapeRoom|1|1900|700|380|54|0"
  "RoboticsSimulator|0|0|0|0|120|1"
  "OpenWorldDemo|1|4600|2100|1300|85|0"
  "PlatformerPrototype|0|95|30|0|19|0"
  "CityBuilderTest|1|780|310|210|41|0"
  "FlightSimShowcase|0|1600|500|0|3|0"
)

mk_file() {
  # $1 path  $2 size in MB
  local path="$1" mb="$2"
  mkdir -p "$(dirname "$path")"
  if [ "$mb" -gt 0 ]; then
    dd if=/dev/urandom of="$path" bs=1048576 count="$mb" 2>/dev/null
  fi
}

for row in "${PROJECTS[@]}"; do
  IFS='|' read -r name cpp inter ddc bin age clean <<< "$row"
  root="$DEMO/$name"
  mkdir -p "$root/Content" "$root/Config" "$root/Saved/Logs"
  echo '{"EngineAssociation": "5.8"}' > "$root/$name.uproject"
  echo '{"enabled": true, "min_age_days": 14}' > "$root/.ueclean.json"
  # Freshness reads mtimes under Content/Source/Config -- an empty Content
  # dir (the case for every Blueprint-only project here) gives no signal at
  # all, so age comes back None and every one of them reads as ineligible
  # regardless of the intended age. Real projects always have real content.
  echo "placeholder" > "$root/Content/Level.umap.placeholder"
  echo "placeholder" > "$root/Config/DefaultEngine.ini"

  if [ "$cpp" = "1" ]; then
    mkdir -p "$root/Source/$name"
    echo "// placeholder" > "$root/Source/$name/$name.cpp"
  fi

  if [ "$clean" != "1" ]; then
    mk_file "$root/Intermediate/Build/placeholder.o" "$inter"
    mk_file "$root/DerivedDataCache/placeholder.ddc" "$ddc"
    [ "$bin" -gt 0 ] && mk_file "$root/Binaries/Mac/placeholder.dylib" "$bin"
  fi

  # Backdate every real file so age math is unambiguous.
  stamp=$(date -v-"${age}"d +%Y%m%d%H%M 2>/dev/null || date -d "-${age} days" +%Y%m%d%H%M)
  find "$root" -type f -exec touch -t "$stamp" {} \;
done

echo "Built ${#PROJECTS[@]} demo projects under $DEMO"
du -sh "$DEMO"
