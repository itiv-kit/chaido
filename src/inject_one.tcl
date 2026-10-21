# One fault injection on the live aiesimulator at a PC-exact coordinate (deterministic across
# runs; a step-count from entry is not): break at targetPC, re-catch OCC times, inject, resume.
# argv: <work-dir> <memsock-url> <addr-key e.g.25,2> <iss-tile e.g.25_2> <targetPC hex>
#       <OCC> <class none|dm|iss> <site hexaddr|issname> <bit> <allkeys> <outfile> <explines> <maxcyc>
set BUILD [lindex $argv 0]; set MEMSOCK [lindex $argv 1]; set KEY [lindex $argv 2]
set TILE  [lindex $argv 3]; set TARGETPC [lindex $argv 4]; set OCC [lindex $argv 5]
set CLASS [lindex $argv 6]; set SITE [lindex $argv 7]; set BIT [lindex $argv 8]
set ALLKEYS [lindex $argv 9]
set OUTFILE [lindex $argv 10]
set EXPLINES [lindex $argv 11]
set MAXCYC  [lindex $argv 12]  ;# runaway ceiling = reference_cycles * multiplier (0 = disabled)
if {$EXPLINES eq ""} {set EXPLINES 0}; if {$MAXCYC eq ""} {set MAXCYC 0}
set ISS "::tl.aie_logical.aie_xtlm.math_engine.array.tile_${TILE}.cm.proc.iss"
proc P {s} { puts "INJ| $s"; flush stdout }
catch {connect -server xrt_server -port 0}
source /tools/xilinx/Vitis/2024.2/scripts/vitis/util/aie_debug_init.tcl
# aiesim_utils.tcl (iss path only) is sourced later: it redefines init_aie_debug and breaks
# enumeration. Retry the connect — MemSock can refuse for a few seconds at boot, and
# init_aie_debug returns a benign error even on success, so break on a live target, not its rc.
for {set t 0} {$t < 40} {incr t} {
  catch {init_aie_debug -work-dir $BUILD -name fi -memsock-url $MEMSOCK -sim-type memserver -stop-at-entry} m
  if {[llength [targets]] > 0} { P "connected after $t retries (targets=[llength [targets]])"; break }
  after 1000
}
after 500
set ::addrs [::aie_debug::get_aie_core_addresses $BUILD]
set ::base [dict get $::addrs $KEY addr]
proc A {o} { return [format 0x%x [expr {$::base + $o}]] }
# Release the target + any other active cores in ALLKEYS ("-"/empty for single-tile); the
# breakpoint is armed only on the target, so a multi-tile pipeline runs while we inject it.
set ::RELBASES [list $::base]
if {$ALLKEYS ne "" && $ALLKEYS ne "-"} {
  foreach k [split $ALLKEYS ";"] {
    if {$k eq $KEY || $k eq ""} continue
    if {[catch {set b [dict get $::addrs $k addr]}]} continue
    lappend ::RELBASES $b
  }
}
proc release_all {} { foreach b $::RELBASES { wrabs [format 0x%x [expr {$b + 0x32010}]] 0x0 } }
proc selm {} { catch {targets -set -filter {name=~"MemSock*"}} }
proc rdabs {a} { selm; set v ""; if {[catch {set v [mrd -force $a 1]} e]} {return -1}
  if {[regexp {:\s*([0-9A-Fa-f]+)} $v -> h]} {return [expr 0x$h]}; return -1 }
proc wrabs {a v} { selm; catch {mwr -force $a $v} }
proc pc {} { return [rdabs [A 0x30280]] }

P "base=[format 0x%x $::base] entry-PC=[pc] CORESTA=[format 0x%x [rdabs [A 0x32004]]] release_cores=[llength $::RELBASES]"
# arm the breakpoint on the target, release all; re-release only the target for each occurrence.
wrabs [A 0x32010] 0x1
wrabs [A 0x32020] [expr {0x80000000 | ([expr $TARGETPC] & 0x3FFF)}]
wrabs [A 0x32018] 0x1
release_all
set got 0
for {set occ 0} {$occ < $OCC} {incr occ} {
  if {$occ > 0} { wrabs [A 0x32010] 0x0 }   ;# re-release ONLY the target for the next occurrence
  set hit 0
  for {set i 0} {$i<600} {incr i} { after 5; if {[rdabs [A 0x3201c]] == 2} {set hit 1; break} }
  if {!$hit} { P "WARN: occurrence [expr {$occ+1}]/$OCC NOT caught (PC=[format 0x%x [pc]])"; break }
  incr got
}
P "targetPC $TARGETPC : caught $got/$OCC occurrences; halted PC=[format 0x%x [pc]] DBGSTA=[format 0x%x [rdabs [A 0x3201c]]]"
# clear breakpoint so it doesn't re-fire during/after inject
wrabs [A 0x32020] 0x0; wrabs [A 0x32018] 0x0

# If (targetPC, occurrence) wasn't reached, don't inject at a wrong PC (bogus MASKED): mark invalid.
if {$got < $OCC} {
  P "NOTCAUGHT: targetPC $TARGETPC occurrence $OCC unreachable (got $got) -> NO injection (invalid coordinate)"
  wrabs [A 0x32010] 0x0
  catch {disconnect}
  P "INJDONE"; exit
}

# Commit-verify: flip must read back as intended; else NOCOMMIT so the driver resamples (no bogus MASKED).
set committed 1
if {$CLASS eq "dm"} {
  set a $SITE; set old [rdabs $a]; set new [expr {$old ^ (1<<$BIT)}]; wrabs $a $new
  set rb [rdabs $a]
  if {$rb != $new} { set committed 0 }
  P "DM inject @ $a bit $BIT : [format 0x%x $old] -> [format 0x%x $rb] (want [format 0x%x $new]) commit=$committed"
} elseif {$CLASS eq "iss"} {
  catch {source /tools/xilinx/Vitis/2024.2/scripts/vitis/util/aiesim_utils.tcl}  ;# RPC helpers (safe now: enumeration done)
  set RPCPORT [expr {[lindex [split $MEMSOCK ":"] 1] + 1}]   ;# RPC port = MemSock port + 1 (per-run)
  set crc [catch {::aiesim::connect localhost:$RPCPORT} ce]
  if {$crc==0} { set s $::aiesim::iss_sock
    set old 0; catch {set old [::aiesim::iss_cmd $s "$ISS get $SITE 0"]}
    set new [expr {$old ^ (1<<$BIT)}]
    catch {::aiesim::iss_cmd $s "$ISS put $new $SITE 0"}
    set rb 0; catch {set rb [::aiesim::iss_cmd $s "$ISS get $SITE 0"]}
    catch {::aiesim::disconnect}
    if {$rb ne $new} { set committed 0 }
    P "ISS inject $SITE bit $BIT : old=$old -> rb=$rb (want $new) commit=$committed"
  } else { set committed 0; P "WARN iss connect failed: $ce" }
} else { P "no injection (control)"; set committed 1 }

if {$CLASS ne "none" && !$committed} {
  P "NOCOMMIT: $CLASS $SITE bit $BIT did not commit (over-width/non-injectable) -> resample"
}
wrabs [A 0x32010] 0x0
P "resumed; PC=[format 0x%x [pc]]"

# ---- cycle-based watchdog ----
# Poll the AIE cycle timer (0x340f8) over MemSock + the output; cycle-based, not wall-clock, so
# host load can't cause false outcomes. COMPLETED=output full; HANG=timer frozen with no output;
# RUNAWAY=timer past MAXCYC (termination broken); POLL_BROKEN=timer unreadable while sim alive.
proc tmr {} { return [rdabs [A 0x340f8]] }
proc outlines {f} { if {[catch {set fh [open $f r]}]} {return -1}; set n 0
  while {[gets $fh ln] >= 0} { if {![regexp {^[XT]} $ln]} {incr n} }; close $fh; return $n }
set prev -1; set stall 0; set nonecnt 0; set wd COMPLETED
set STALL_SAMPLES 8; set MAXNONE 12
for {set p 0} {$p < 600} {incr p} {
  after 1500
  set cyc [tmr]
  set ol [outlines $OUTFILE]
  if {$cyc < 0} {   ;# timer unreadable -> sim likely exited, or MemSock/tool broke
    incr nonecnt
    if {$ol >= $EXPLINES && $EXPLINES > 0} { set wd COMPLETED; break }
    if {$nonecnt >= $MAXNONE} { set wd POLL_BROKEN; break }
    continue
  }
  set nonecnt 0
  if {$EXPLINES > 0 && $ol >= $EXPLINES} { set wd COMPLETED; break }
  if {$MAXCYC > 0 && $cyc > $MAXCYC} { set wd RUNAWAY; break }
  if {$cyc == $prev} { incr stall
    if {$stall >= $STALL_SAMPLES && $ol <= 0} { set wd HANG; break }
  } else { set stall 0 }
  set prev $cyc
}
P "WATCHDOG $wd cyc=$prev outlines=[outlines $OUTFILE]"
catch {disconnect}
P "INJDONE"; exit
