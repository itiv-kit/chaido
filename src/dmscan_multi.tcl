# Scan used data-memory across several tiles (core + neighbours) of one live sim.
# argv: <work-dir> <memsock-url> <";"-sep KEYs e.g. "25,2;25,3;26,2">
# Per KEY: release the core so buffers fill, halt, scan DM for non-fill words, print
# "RANGE <key> 0xSTART 0xEND words". A missing tile is skipped.
set BUILD [lindex $argv 0]; set MEMSOCK [lindex $argv 1]; set KEYS [lindex $argv 2]
proc P {s} { puts "DM| $s"; flush stdout }
catch {connect -server xrt_server -port 0}
source /tools/xilinx/Vitis/2024.2/scripts/vitis/util/aie_debug_init.tcl
# retry until the MemSock target appears (bigger sims refuse the connection for a few s)
for {set t 0} {$t < 40} {incr t} {
  catch {init_aie_debug -work-dir $BUILD -name fi -memsock-url $MEMSOCK -sim-type memserver -stop-at-entry} m
  if {[llength [targets]] > 0} { P "connected after $t retries"; break }
  after 1000
}
after 500
set ADDRS [::aie_debug::get_aie_core_addresses $BUILD]
P "AVAILKEYS [dict keys $ADDRS]"
proc sel {} { catch {targets -set -filter {name=~"MemSock*"}} }
# release ALL requested cores first so every tile's buffers get populated, then scan each
foreach key [split $KEYS ";"] {
  if {[catch {set b [dict get $ADDRS $key addr]}]} { P "SKIP $key (no addr)"; continue }
  sel; catch {mwr -force [format 0x%x [expr {$b + 0x32010}]] 0x0}
}
after 3000
set FILL1 0x55555555; set FILL2 0xffffffff
foreach key [split $KEYS ";"] {
  if {[catch {set b [dict get $ADDRS $key addr]}]} { continue }
  sel; catch {mwr -force [format 0x%x [expr {$b + 0x32010}]] 0x1}
  after 200
  P "TILE $key base=[format 0x%x $b]"
  set used 0; set rstart -1; set rprev -1
  for {set off 0} {$off < 0x20000} {incr off 4} {
    sel; set v ""
    if {[catch {set v [mrd -force [format 0x%x [expr {$b + $off}]] 1]}]} { continue }
    if {![regexp {:\s*([0-9A-Fa-f]+)} $v -> h]} continue
    set val [expr 0x$h]
    if {$val != $FILL1 && $val != $FILL2 && $val != 0} {
      incr used
      if {$rstart < 0} { set rstart $off }
      set rprev $off
    } else {
      if {$rstart >= 0 && [expr {$off - $rprev}] > 64} {
        P [format "RANGE %s 0x%x 0x%x %d" $key [expr {$b+$rstart}] [expr {$b+$rprev+4}] [expr {($rprev-$rstart)/4+1}]]
        set rstart -1
      }
    }
  }
  if {$rstart >= 0} { P [format "RANGE %s 0x%x 0x%x %d" $key [expr {$b+$rstart}] [expr {$b+$rprev+4}] [expr {($rprev-$rstart)/4+1}]] }
  P "USED $key words=$used bits=[expr {$used*32}]"
}
catch {disconnect}
P "DMDONE"
exit
