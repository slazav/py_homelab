import subprocess
import select
import re
import numpy
import time

###########################################################
###########################################################
# SPP interface: I'm using https://github.com/slazav/device2
# to access devices.
class SPP:

  def __init__(self, prog, timeout=10):
    self.timeout = timeout
    self.p = subprocess.Popen(prog,
                        shell=1, text=1,
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE)
    self.read()

  def read(self):
    buf=''
    r, w, e = select.select([ self.p.stdout ], [], [], self.timeout)
    if not self.p.stdout in r: raise Exception('SPP read timeout')
    for line in self.p.stdout:
      if re.match('#OK', line): return buf
      if re.match('#Error', line): raise Exception(line)
      buf+=line
    raise Exception('SPP communication error: #OK or #Error expected')

  def query(self,cmd):
    print(cmd, file=self.p.stdin)
    self.p.stdin.flush()
    return self.read()

###########################################################

def set_int_gen(osc, amp, freq):
  osc.query('gen_builtin 0 %f %f sine'%(amp, freq))

def set_ext_gen1(gen, amp, freq):
  gen.query(':w20=1,0.')  # status
  gen.query(':w21=0.')    # waveform
  gen.query(':w23=%d,0.'%(int(freq*100))) # freq
  gen.query(':w25=%d.'%(int(amp*1000)))   # amp
  gen.query(':w27=1000.') # offset

def set_ext_gen2(gen, amp, freq):
  gen.query(':w20=0,1.')  # status
  gen.query(':w22=0.')    # waveform
  gen.query(':w24=%d,0.'%(int(freq*100))) # freq
  gen.query(':w26=%d.'%(int(amp*1000)))   # amp
  gen.query(':w28=1000.') # offset

# measure channel A
def measureB(osc, rng, npts, dt, fmin=0, fmax=1e7):
  fname="/tmp/rec1.sig"
  osc.query('trig_set NONE 0 RISING 0')
  osc.query('chan_set B 1 AC %f'%(rng))
  osc.query('block B 0 %d %e %s'%(npts,dt,fname))
  osc.query('wait')

  # detect overload flag
  res = subprocess.run('sig_filter -f overload %s'%(fname),
                        text=1, shell=1, capture_output=1)
  ovl = ('1' in res.stdout.split())
  # get amplitude and frequency
  res = subprocess.run('sig_filter -f lockin %s -s 0 -F %e -G %e -r 0'%(fname, fmin, fmax),
                        text=1, shell=1, capture_output=1)
  r = res.stdout.split()
  if len(r)!=3: (F,V) = (None,None)
  else: (F,V) = (float(r[0]), 2*float(r[1]))
  return (F, V, ovl)

# measure channels AB
def measureAB(osc, rng, npts, dt, fmin=0, fmax=1e7):
  fname="/tmp/rec1.sig"
  osc.query('trig_set NONE 0 RISING 0')
  osc.query('chan_set A 1 AC %f'%(rng))
  osc.query('chan_set B 1 AC %f'%(rng))
  osc.query('block AB 0 %d %e %s'%(npts,dt,fname))
  osc.query('wait')
  # detect overload flag
  res = subprocess.run('sig_filter -f overload %s'%(fname),
                        text=1, shell=1, capture_output=1)
  ovl = ('1' in res.stdout.split())
  # get amplitudes and frequencies
  res1 = subprocess.run('sig_filter -f lockin %s -s 0 -F %e -G %e -r 0'%(fname, fmin, fmax),
                        text=1, shell=1, capture_output=1)
  res2 = subprocess.run('sig_filter -f lockin %s -s 1 -F %e -G %e -r 1'%(fname, fmin, fmax),
                        text=1, shell=1, capture_output=1)
  r1 = res1.stdout.split()
  r2 = res2.stdout.split()
  if len(r1)!=3: (F1,V1) = (None,None)
  else: (F1,V1) = (float(r1[0]), 2*float(r1[1]))
  if len(r2)!=3: (F2,V2) = (None,None)
  else: (F2,V2) = (float(r2[0]), 2*float(r2[1]))
  return (F1,V1, F2,V2, ovl)

##################################################
# Interface to oscilloscope (Pico4262, device name: "osc")
class osc(SPP):
  def __init__(self):
    super().__init__('device_c use_dev osc', timeout=20)
    self.rngsA = self.query('ranges A').split()
    self.rngsB = self.query('ranges B').split()

  # ret range
  def set_range(self, ch, volt):
    for r in self.rngsA:
      rng = float(r)
      if rng>volt: break
    self.query('chan_set %s 1 AC %f'%(ch, rng))
    return rng

  # Measure any combination of channels 'A', 'AB', etc.
  # Return array with (fre,vpp,ovl) touples for all channels.
  # Channel ranges should be set before!
  def measure(self, chs, npts, dt, fmin=0, fmax=1e7):
    fname="/tmp/rec1.sig"
    self.query('trig_set NONE 0 RISING 0')
    self.query('block %s 0 %d %e %s'%(chs, npts,dt,fname))
    self.query('wait')

    ret=[]
    for i in range(len(chs)):
      # detect overload flag
      res = subprocess.run('sig_filter -c %d -f overload %s'%(i, fname),
                           text=1, shell=1, capture_output=1)
      ovl = ('1' in res.stdout.split())
      # get amplitudes and frequencies

      res = subprocess.run('sig_filter -f lockin %s -s %d -F %e -G %e -r %d'%(fname, i, fmin, fmax, i),
                          text=1, shell=1, capture_output=1)
      r = res.stdout.split()
      if len(r)!=3: ret.append((0,0,0))
      else: ret.append((float(r[0]), 2*float(r[1]), int(ovl)))
    return ret

  # Measure, with autorange.
  # Rngs parameter should contain initial guess for range.
  # It will be updated after run
  def measure_autorange(self, chs, rngs, npts, dt, fmin=0, fmax=1e7):

    while True:
      # set range
      for i in range(len(chs)):
        rngs[i] = self.set_range(chs[i], rngs[i])
      #print("# M: ", rngs)
      ret = self.measure(chs, npts, dt, fmin=fmin, fmax=fmax)

      repeat=False
      for i in range(len(ret)):
        if ret[i][2]:
          #print("  #ovl: ", ret[i], rngs[i])
          rngs[i] *= 1.2
          repeat=True
      if not repeat: break
    return ret;

  # set internal generator
  def set_sine(self, amp, freq):
    self.query('gen_builtin 0 %f %f sine'%(amp, freq))

  def set_zero(self):
    self.query('gen_builtin 0 0 1 dc')


##################################################
# Interface to generator (jds6600, device name: "gen")
class gen(SPP):
  def __init__(self):
    super().__init__('device_c use_dev gen', timeout=10)


  def write_reg(self, reg, *args):
    args = ','.join(map(str,map(int,args)))
    self.query(":w%02d=%s."%(reg, args))

  ##############
  # set status of both channels
  def set_chans(self, ch1, ch2):
    self.write_reg(20, ch1,ch2)

  # set sine wave (ch = 0,1)
  def set_sine(self, ch, amp, freq):
    ch = int(bool(ch))
    self.write_reg(21+ch, 0) # waveform
    self.write_reg(23+ch, int(freq*100)) # freq
    self.write_reg(25+ch, int(amp*1000)) # amp
    self.write_reg(27+ch, 1000)          # offset

  def set_zero(self, ch):
    ch = int(bool(ch))
    self.write_reg(21+ch, 0) # waveform
    self.write_reg(23+ch, 0) # freq
    self.write_reg(25+ch, 0) # amp
    self.write_reg(27+ch, 1000)          # offset

  def set_dc(self, ch, amp):
    ch = int(bool(ch))
    v = int(amp*100+1000)
    if v > 1999: v=1999
    if v < 1: v=1
    #self.write_reg(20, 0,1)  # channel status
    self.write_reg(21+ch, 6)  # waveform
    self.write_reg(23+ch, 0)  # freq
    self.write_reg(25+ch, 0)  # amp
    self.write_reg(27+ch, v)  # offset

##################################################
# Interface to power supply (HM310T, device name: "ps")
class ps(SPP):
# *  *idn? -- get ID string (set artificially in the driver)
# *  out? -- get output state, 0 or 1
# *  stat:raw? -- protection status mask (raw data)
# *  stat? -- protection status in human-readable form (could be incomplete)
# *  spec:raw? -- "specification and type", no idea what is it, for my device it is.
# *  tail:raw? -- "tail classification", no idea what is it, , for my device it is.
# *  dpt:raw? -- return decimal point positions as raw data
# *  dpt? -- return decimal point positions for volts, amps, watts (should be "2 3 3")
# *  volt:meas? -- return measured voltage [V]
# *  curr:meas? -- return measured current [A]
# *  pwr:meas?  -- return measured power [W] (does not work?)
# *  volt? -- return voltage set value [V]
# *  curr? -- return current set value [A]
# *  ovp?  -- get over voltage protection [V]
# *  ocp?  -- get over current protection [A]
# *  opp?  -- get over power protection [W] (doesn not work?)
# *  addr? -- get modbus slave address (should be 1)
# *  out [0|1] -- set output state
# *  volt <volts> -- set voltage
# *  curr <amps> -- set current
# *  ovp <volts> -- set over voltage protection
# *  ocp <amps> -- set over current protection
# *  opp <watts> -- set over power protection

  def __init__(self):
    super().__init__('device_c use_dev ps', timeout=10)

  def set_out(self, state):
    self.query("out %d"%(state!=0))

  def set_i(self, curr):
    self.query("curr %f"%(curr))

  def set_v(self, volt):
    self.query("volt %f"%(volt))

  def set_iv(self, curr, volt):
    self.query("volt %f"%(volt))
    self.query("curr %f"%(curr))
    self.query("out 1")

  def get_v(self):
    return float(self.query("volt:meas?"))


  def get_i(self):
    return float(self.query("curr:meas?"))

  def get_iv(self):
    return (float(self.query("curr:meas?")), float(self.query("volt:meas?")))


##################################################
# Lock-in measurement 

def meas_sweep(
      fname="tmp.dat",  # file to save result
      amps=None,        # amplitude list
      freqs=None,       # frequency list
      ext_gen=False,    # use internal/external generator
      ext_gen_ch=0,     # generator channel
      chs='AB',         # oscilloscope channel(s)
      dt=1e-7,          # sampling step
      periods=100,      # number of periods to measure
    ):

  # set default frequency list
  if type(freqs)==type(None):
    if ext_gen:       freqs = numpy.geomspace(1e3, 4e6, fpts)
    else: freqs = numpy.geomspace(1e3, 2e4, fpts)

  # open oscilloscope and get ranges
  osc0 = osc()

  # open external generator if needed
  if ext_gen:
    gen0=gen()
    gen0.set_chans(ext_gen_ch==0,ext_gen_ch==1)

  # open output file and pring header
  ff = open(fname, "w")
  print("# F_set      Vpp_set   F1           Vpp1   OVL1 RNG1  F2           Vpp2   OVL2 RNG2 ", file=ff)

  rngs=[amps[0],amps[0]]
  for amp in amps:
    for freq in freqs:

      # set sine wave
      if ext_gen: gen0.set_sine(ext_gen_ch, amp, freq)
      else: osc0.set_sine(amp, freq)
      time.sleep(0.1)

      # do measurement (autorange)
      npts=periods/dt/freq
      ret = osc0.measure_autorange(chs, rngs, npts, dt, fmin=freq*0.95, fmax=freq*1.05)

      s = "%.6e %.6f"%(freq, amp)
      for i in range(len(ret)):
        s+= "  %.6e %.6f %d %.0e"%(ret[i][0], ret[i][1], ret[i][2], rngs[i])
        rngs[i] = ret[i][1]
      print(s)
      print(s, file=ff)
      ff.flush()

  # switch generator off
  if ext_gen: gen0.set_zero(ext_gen_ch)
  else: osc0.set_zero()
  ff.close()



def meas_amp_sweep(
      fname="tmp.dat",  # file to save result
      freq=1000,        # measurement frequency
      amps=None,        # list of amplitudes (default: linspace(0,2,101))
      ext_gen=False,    # use internal/external generator
      ext_gen_ch=0,     # channel
      osc_ch='A',       # oscilloscope channel
      osc_ch_ref=None,  # if not None, measure input voltage on this channel
      rng_mult=1.0, rng_min=0,
      autorange=True,
      dt=1e-7,          # sampling step
      periods=100,      # number of periods to measure
    ):     #

  # set default amplitude list
  if type(amps)==type(None):
    if ext_gen: amps = numpy.linspace(0, 2, 101)
    else:       amps = numpy.linspace(0, 10, 101)

  # open oscilloscope and get ranges
  osc0 = osc()
  rngs = osc0.query('ranges %s'%(osc_ch)).split()

  # open external generator if needed
  if ext_gen:
    gen0=gen()
    gen0.set_chans(ext_gen_ch==0,ext_gen_ch==1)

  # open output file and pring header
  ff = open(fname, "w")
  print("# F_in  Vpp_in  F_out Vpp_out OVL", file=ff)

  npts=periods/dt/freq
  r1=0
  r2=0
  for amp in amps:

    if not autorange:
      # find measurement ranges
      for r1 in range(len(rngs)):
        rng = float(rngs[r1])
        if rng>rng_mult*amp and rng>=rng_min: break
      for r2 in range(len(rngs)):
        rng = float(rngs[r2])
        if rng>amp and rng>=rng_min: break

    # set sine wave
    if ext_gen: gen0.set_sine(ext_gen_ch, amp, freq)
    else: osc0.set_sine(amp, freq)
    time.sleep(0.1)

    # do measurement (autorange loop)
    while True:
      flags=""
      if osc_ch_ref:
        r = osc0.measure2(npts=npts, dt=dt,
                          fmin=freq*0.95, fmax=freq*1.05,
                          rng1=float(rngs[r1]), rng2=float(rngs[r2]),
                          ch1=osc_ch, ch2=osc_ch_ref)
        if len(r)!=6: raise Exceptopn("Bad output")
        in_freq = r[3]
        in_amp = r[4]
        if r[2]:
           flags+=" OVL"
           if autorange: r1+=1
        if r[5]: 
           flags+=" OVL_REF"
           if autorange: r2+=1
        #print("# %f(%f) %s %f(%f) %s"%(r[1],rng,r[2], r[4],ref_rng, r[5]))
      else:
        r = osc0.measure(osc_ch, float(rngs[r1]), npts, dt, fmin=freq*0.95, fmax=freq*1.05)
        if len(r)!=3: raise Exceptopn("Bad output")
        in_freq = freq
        in_amp = amp
        if r[2]:
          flags+=" OVL"
          if autorange: r1+=1

      if flags!="": flags = "# " + flags
      print(in_freq, in_amp, r[0], r[1], flags)
      if not autorange or flags=="" or r1>=len(rngs) or r2>=len(rngs): break

    # print results
    print(in_freq, in_amp, r[0], r[1], flags, file=ff)
    ff.flush()

  # switch generator off
  if ext_gen: gen0.set_zero(ext_gen_ch)
  else: osc0.set_zero()
  ff.close()

