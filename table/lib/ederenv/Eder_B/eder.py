import sys
import time
import math
import version
from threading import Timer
try:
    sys.path.append('../')
    import connect_unit as cu
except:
    sys.path.append('../../')
    import connect_unit as cu
class Eder:
    import csv
    import background
    import common

    chip_types = {0x02731803:'Eder B', 0x02741812:'Eder B MMF'}

    def __init__(self, init=False, backgr=False, log_instance=None, unit_name='Motherboard 1', evkplatform_type='MB1', rfm_type='rfm_3.0', ref_freq=None):
        import evkplatform
        import gpio
        import register
        import memory
        import ref
        import vco
        import pll
        import dig_pll
        import adc
        import bf
        import temp
        import eeprom   
        import otp
        import rx
        import tx
        import trx_soft_sw
#        import ext
        import mbist
        import test
        import eder_status
        import evk_logger
        import device_info

        self.evkplatform_type = evkplatform_type
        self.pdet_bias = None

        self.evkplatform = evkplatform.EvkPlatform(evkplatform_type)

        if log_instance == None:
            self.logger = evk_logger.EvkLogger()
        else:
            self.logger = log_instance

        self.logger.log_info('EVK SW Version {}'.format(version.version_num),2)

        if self.evkplatform_type == 'MB1':
            if self.evkplatform.init(unit_name, 'rfm_siv') != 0:
                print ('  Device initialization failed!')
                return
            self.evkplatform.setvcm(600)

        self.rpi = gpio.EderGpio(self.evkplatform_type)
        # Start with generic register map
        self.regs = register.Register(self.evkplatform_type)

        self.check_chip_type()

        # Select correct register map
        self.regs = register.Register(self.evkplatform_type, self.chip_type)

        self.mems = memory.Memory(self.evkplatform_type)
        self.status = eder_status.EderStatus()
        #
        
        self.otp  = otp.Otp()
        self.pll  = pll.Pll()
        self.dig_pll = dig_pll.Dig_Pll()
        self.adc  = adc.Adc()
        self.adc.init()

        #
        self.eeprom = eeprom.Eeprom(self.evkplatform_type)
        self.temp = temp.Temp()

        device_info = device_info.DeviceInfo()
        device_info.set_attrib('rfm_type', rfm_type)

        self.rx = rx.Rx()
        self.tx = tx.Tx()

        self.mbist = mbist.Mbist(self.evkplatform_type)
        
        self.trx_soft_sw = trx_soft_sw.TrxSoftSw()
        self.mode = None
        self.chip_present_status = False

        #self.ext = ext.Ext(self)

        self.test = test.Test(self)

        # Initialisation
        if init:
            self.init()            
        else:
            self.check()

        if self.evkplatform_type == 'MB1':
            if self.check_3_3V() == False:
                # 3.3V not reaching chip
                # Turn off 3.3V
                self.evkplatform.drv.pwraoff()
                print ''
                self.logger.log_info('HARDWARE FAILURE: 3.3V not reaching chip. Disabling 3.3V. !!!!',2)
                print ''

        # Background daemons
        if backgr:
            self.com = self.background.BackgroundTask(5, self.bg_check)

        #self.chip_mon = self.background.BackgroundTask(300, self.chip_monitor)

    def init(self,do_print=False):
        self.reset()
        if self.check() == True:
            #if self.regs.verify('default',do_print):
            self.pll.init()
            self.temp.init()
            self.status.set_mode(self.status.SX_MODE)
            self.logger.log_info('Chip init into SX mode.',2)
            return True
        return False

    def ver(self):
        self.logger.log_info('EVK SW Version {}'.format(version.version_num),2)

    def fpga_clk(self, mode):
        if (mode == 1):
            self.regs.set('fast_clk_ctrl',0x02)
        else:
            self.regs.clr('fast_clk_ctrl',0x00)

    def reset(self, rst_time_in_ms=1):
        self.pll.reset()
        self.adc.reset()
        self.temp.reset()
        self.rx.reset()
        self.tx.reset()
        self.mode = None
        self.rpi.reset(rst_time_in_ms)
        self.logger.log_info('Chip reset.',2)

    def check_3_3V(self):
        ok_3_3v = True
        self.adc.start(0x83,None,4)
        if self.adc.mean() == 0xFFF:
            ok_3_3v = False
        self.adc.stop()
        return ok_3_3v

    def check(self):
        chip_id = self.regs.rd('chip_id')
        if self.chip_is_present():
            self.logger.log_info('Chip present (chip_id = 0x{:0{}X}).'.format(chip_id,8),2)
            return True
        else:
            self.logger.log_info('No chip present (chip_id = 0x{:0{}X})!'.format(chip_id,8),2)
            return False
        
    def bg_check(self):
        if self.chip_present_status == False:
            if self.chip_is_present():
                chip_id = self.regs.rd('chip_id')
                self.logger.log_info('Chip became present (chip_id = 0x{:0{}X})!'.format(chip_id,8),2)
                return True
            return False
        else:
            if self.chip_is_present() == False:
                chip_id = self.regs.rd('chip_id')
                self.logger.log_info('Chip disconnected (chip_id = 0x{:0{}X})!'.format(chip_id,8),2)
                return False
            return True

    def check_chip_type(self):
        chip_id = self.regs.rd('chip_id')
        try:
            self.chip_type = Eder.chip_types[chip_id]
        except:
            self.chip_type = 'unknown'
        self.logger.log_bold('Chip type {} detected.'.format(self.chip_type), 2)
        
    def chip_is_present(self):
        chip_id = self.regs.rd('chip_id')
        try:
            if (Eder.chip_types[chip_id] == self.chip_type):
                self.chip_present_status = True
                return True
            else:
                self.chip_present_status = False
                return False
        except KeyError:
            self.chip_present_status = False
            return False

    def chip_monitor(self):
        temperature = self.temp.run()
        self.logger.log_info(temperature)

    # RX
    def rx_setup(self, freq, pll_setup=True):
        if pll_setup:
            self.pll.init()
            self.pll.set(freq)
            time.sleep(1)
        self.rx.setup(freq)

    def rx_enable(self):
        self.rx.enable()
        self.mode = 'RX'

    def rx_disable(self):
        self.rx.disable()
        self.mode = None


    # TX
    def tx_setup(self, freq, pll_setup=True):
        if pll_setup:
            self.pll.init()
            self.pll.set(freq)
            time.sleep(0.5)
        self.tx.setup(freq)
        
        # Restore some registers from RFM EEPROM if valid
        rfm_id = self.get_rfm_id()
        if freq == 58.32e9:
            channel = 1
        elif freq == 60.48e9:
            channel = 2
        elif freq == 62.64e9:
            channel = 3
        elif freq == 64.8e9:
            channel = 4
        else:
            channel = 0

        if not self.restore_settings(channel):
            # Read from json file
            json_file_name = rfm_id + '.json'
            self.logger.log_info('Checking if settings-file "{0}" exists.'.format(json_file_name),2)
            try:
                tx_json_regs = json.read(json_file_name, print_error=False)
                self.logger.log_info('Reading ' + json_file_name,4)
                eder.regs.dump(tx_json_regs)
            except:
                self.logger.log_warning('No settings-file exist.',4)
                self.logger.log_info('Using normal setup.',2)

        self.logger.log_info('TX setup complete',2)

    def tx_enable(self):
        self.tx.enable()
        self.mode = 'TX'
		
    def tx_disable(self):
        self.tx.disable()
        self.mode = None
		

    # TRX
    def trx_setup(self, freq):
        self.pll.init()
        self.pll.set(freq)
        self.logger.log_info('PLL setup complete',2)
        self.tx_setup(freq, pll_setup=False)
        self.rx_setup(freq, pll_setup=False)

    def trx_enable(self, trx='tgl'):
        if trx == 'tgl':
            if self.mode == None:
                self.tx_disable()
                self.rx_enable()
            elif self.mode == 'RX':
                self.rx_disable()
                self.tx_enable()
            elif self.mode == 'TX':
                self.tx_disable()
                self.rx_enable()
        elif trx == 'rx':
            self.tx_disable()
            self.rx_enable()
        elif trx == 'tx':
            self.rx_disable()
            self.tx_enable()

    def trx_hw_sw_enable(self):
        self.tx.hw_sw_enable()
        self.rx.hw_sw_enable()
        
    def trx_hw_sw_disable(self):
	self.rpi.trx_mode_disable()
        self.tx.hw_sw_disable()
        self.rx.hw_sw_disable()



    # LOOP
    def loop_setup(self, freq):
		self.pll.init()
		self.pll.set(freq)
		self.tx.setup(freq)
		self.regs.wr('bias_ctrl_rx',0x14242)
		self.rx.setup(freq)
		self.regs.wr('bias_tx',0x97FE)
		self.regs.wr('tx_rf_gain',0xc)
		self.regs.wr('bias_ctrl_tx',0x14242)
		self.logger.log_info('Loop setup complete')
		self.regs.wr('tx_rx_sw_ctrl',0x06)
		self.regs.wr('tx_rf_gain',0x0F)

    def loopback_setup(self, freq):
        import rx
        import tx
        self.reset()
        self.pll.init()
        self.pll.set(freq)
        self.tx.setup(freq)
        self.rx.setup(freq)
        self.logger.log_info('Loopback setup complete')

    def loopback_enable(self):
        self.regs.wr('tx_rx_sw_ctrl',0b110)        # bit0=0 for TX or RX enable

    def loopback_disable(self):
        self.regs.wr('tx_rx_sw_ctrl', 0b000)

    def sx_enable(self):
        """Enter SX mode from TX or RX mode
           Example: eder.sx_enable()
        """
        self.rx_disable()
        self.tx_disable()
        self.tx_rx_hw_disable()

    def tx_rx_hw_enable(self):
        """Enables TX/RX mode switching using TX_RX_SW input
        Example: eder.tx_rx_hw_enable()
        """
        self.regs.set('tx_rx_sw_ctrl', 0b001)

    def tx_rx_hw_disable(self):
        """Disables TX/RX mode switching using TX_RX_SW input
        Example: eder.tx_rx_hw_enable()
        """
        self.regs.clr('tx_rx_sw_ctrl', 0b001)

    def get_rfm_id(self):
        if self.evkplatform_type == 'MB1':
            wait_time = 0.01
            rfm_id = ''
            # Check if EEPROM data is valid
            x = self.evkplatform.drv.readeprom(0x80)
            if x!=0xab:
                print 'rfm id not valid!'
                return ''
            for addr in xrange(0x81,0x8A+1):
                x = self.evkplatform.drv.readeprom(addr)
                if x!=0:
                    rfm_id += chr(x)
                elif x==0:
                    return rfm_id 
                time.sleep(wait_time)
        else:
            return ''

    def saveRegisterSettings(self, file_name):
        config = self.regs.dump(do_print=False)
        self.json.write(file_name, config)

    def loadGainSettings(self, file_name):
        config = self.json.read(file_name)
        reduced_config = {}

        # Currently only selected registers will be restored
        restored_registers = ['tx_bb_phase','tx_bb_gain','tx_bb_iq_gain','tx_bfrf_gain','rx_gain_ctrl_bfrf','rx_gain_ctrl_bb1',
                              'rx_gain_ctrl_bb2','rx_gain_ctrl_bb3','trx_tx_on','trx_rx_on']
        for register_name in restored_registers:
            try:
                reduced_config[register_name] = config[register_name]
            except:
                pass
        self.regs.dump_wr(reduced_config)



    def store_settings(self, rfm_id, channel):
        if self.evkplatform_type == 'MB1':
            wait_time = 0.01
            if len(rfm_id) == 0:
                print 'rfm_id not specified'
                return False
            if channel<1 or channel>6:
                self.logger.log_warning('RFM EEPROM channel must be between 1 and 6.',4)
                return False
            # RFM EEPROM ID valid flag 0x80  set to 0xab
            self.evkplatform.drv.writeeprom(0x80, 0xab)
            time.sleep(wait_time)

            # RFM EEPROM ID Addresses 0x81-0x8A
            for addr in xrange(0x81,0x8A+1):
                time.sleep(wait_time)
                if len(rfm_id) > addr-0x81:
                    self.evkplatform.drv.writeeprom(addr, ord(rfm_id[addr-0x81]))
                else:
                    self.evkplatform.drv.writeeprom(addr, 0)

            time.sleep(wait_time)
            # Channel data valid flag set to 0xab
            addr = 0x8B + (channel - 1) * 6
            self.evkplatform.drv.writeeprom(addr, 0xa0 + channel)
            time.sleep(wait_time)

            # Calculate address for channel
            addr = 0x8B + (channel - 1) * 6
            time.sleep(wait_time)
            self.evkplatform.drv.writeeprom(addr+1, self.regs.rd('tx_bb_i_dco'))
            time.sleep(wait_time)
            self.evkplatform.drv.writeeprom(addr+2, self.regs.rd('tx_bb_q_dco'))
            time.sleep(wait_time)
            self.evkplatform.drv.writeeprom(addr+3, self.regs.rd('tx_bb_phase'))
            time.sleep(wait_time)
            self.evkplatform.drv.writeeprom(addr+4, self.regs.rd('tx_bb_iq_gain'))
            time.sleep(wait_time)
            self.evkplatform.drv.writeeprom(addr+5, self.regs.rd('tx_bb_gain'))
            return True
        else:
            return False

    def restore_settings(self, channel):
        self.logger.log_info('Checking if RFM EEPROM is programmed with settings.',2)
        if self.evkplatform_type == 'MB1':
            wait_time = 0.01

            if channel<1 or channel>6:
                self.logger.log_warning('RFM EEPROM channel must be between 1 and 6.',4)
                return False

            # Channel data valid flag set to 0xab
            addr = 0x8B + (channel - 1) * 6
            valid_flag = self.evkplatform.drv.readeprom(addr)
            time.sleep(wait_time)
            if valid_flag != (0xa0+channel):
                self.logger.log_info('RFM EEPROM not programmed.',4)
                return False
            
            # Calculate address for channel
            addr = 0x8B + (channel - 1) * 6
            self.regs.wr('tx_bb_i_dco', self.evkplatform.drv.readeprom(addr+1))
            time.sleep(wait_time)
            self.regs.wr('tx_bb_q_dco', self.evkplatform.drv.readeprom(addr+2))
            time.sleep(wait_time)
            self.regs.wr('tx_bb_phase', self.evkplatform.drv.readeprom(addr+3))
            time.sleep(wait_time)
            self.regs.wr('tx_bb_iq_gain', self.evkplatform.drv.readeprom(addr+4))
            time.sleep(wait_time)
            self.regs.wr('tx_bb_gain', self.evkplatform.drv.readeprom(addr+5))
            return True
        else:
            return False

    def run_rx(self, freq=60.48e9):
        self.reset()
        self.tx_disable()
        if self.evkplatform_type == 'MB1':
            self.evkplatform.drv.setvcm(700)
        self.rx_setup(freq)
        self.rx_enable()

    def run_tx(self, freq=60.48e9):
        self.reset()
        self.rx_disable()
        self.tx_setup(freq)
        self.tx_enable()

    def rdwrtime(self, n):
        import time
        start_time = time.time()
        for i in xrange(0, n):
            self.regs.rd('chip_id_sw_en')
            self.regs.wr('chip_id_sw_en',0)
        elapsed_time = time.time() - start_time
        print elapsed_time * 1000

    def run_tx_lo_leakage_cal(self):
        #self.reset()
        if self.evkplatform_type == 'MB1':
            self.evkplatform.drv.setvcm(600)
        self.init()
        self.pll.init()
        self.pll.set(60.48e9)
        self.tx_setup(0.0, pll_setup=False)
        self.rx.setup_no_dco_cal(0.0)
        self.regs.wr('bias_ctrl_rx',0x13FFC)
        self.regs.wr('bias_ctrl_tx',0x13FFC)
        self.regs.set('tx_rx_sw_ctrl', 0x06)
        self.tx.dco.run()
        tx_bb_i_dco = self.regs.rd('tx_bb_i_dco')
        tx_bb_q_dco = self.regs.rd('tx_bb_q_dco')
        self.rx.disable()
        self.tx.disable()
        self.reset()
        self.regs.wr('tx_bb_i_dco', tx_bb_i_dco)
        self.regs.wr('tx_bb_q_dco', tx_bb_q_dco)

    def tx_pdet_offset_meas(self):
        power = {}
        for pdet_index in range(0x0, 0x10):
            self.adc.start(0x7, 0x80+pdet_index, 7)
            power['TX'+str(pdet_index)] = self.adc.mean()
            self.adc.stop()
        self.pdet_bias = power
        self.logger.log_info('pdet_bias: {}'.format(self.pdet_bias),2)

    def tx_pdet(self, print_res=True, absolut_value=False):
        power = {}
        for pdet_index in range(0x0, 0x10):
            self.adc.start(0x7, 0x80+pdet_index, 7)
            if absolut_value:
                power['TX{:0>2d}'.format(pdet_index)] = self.adc.mean()
            else:
                power['TX{:0>2d}'.format(pdet_index)] = self.adc.mean() - self.pdet_bias['TX'+str(pdet_index)]
            self.adc.stop()
        if print_res:
            counter = 0
            for key in sorted(power.iterkeys()):
                print "%s: %d" % (key, power[key]),
                counter = counter + 1
                if counter == 8:
                    print
        return power

    def vcm_check(self):
        measured_values = self.rx.dco.iq_meas.meas(meas_type='xx')
        time_stamp = self.common.get_time_stamp()
        with open(self.rxbb_vcm_to_csv_file_name, 'ab') as vcm_log:
            writer = self.csv.writer(vcm_log)
            measured_values['idiff'] = self.rx.dco._decToVolt(measured_values['idiff'])
            measured_values['qdiff'] = self.rx.dco._decToVolt(measured_values['qdiff'])
            measured_values['icm'] = self.rx.dco._decToVolt(measured_values['icm'])
            measured_values['qcm'] = self.rx.dco._decToVolt(measured_values['qcm'])
            writer.writerow([time_stamp, round(self.temp.run()-273, 2), measured_values['idiff'], measured_values['qdiff'], measured_values['icm'], measured_values['qcm']])
            vcm_log.close()

    def rxbb_vcm_to_csv(self, minutes=0.5, file_name='vcm_log.csv'):
        """Start logging of RX DC offset to file for a specified period of time (in minutes).
        Default file name: vcm_log.csv
        Default logging duration: 0.5 minutes
        Example: To log for 0.5 minutes to file vcm_log.csv
                 eder.rxbb_vcm_to_csv()
     
                 To log for 5 minutes to file vcm_log.csv
                 eder.rxbb_vcm_to_csv(5)

                 To log for 2 minutes to file vcm_log_2.csv
                 eder.rxbb_vcm_to_csv(2, 'vcm_log_2.csv')
        """
        self.rxbb_vcm_to_csv_file_name = file_name
        with open(file_name, 'ab') as vcm_log:
            writer = self.csv.writer(vcm_log)
            writer.writerow(["%Time", "Temp.", " V_i_diff", " V_q_diff", " V_i_com", " V_q_com"])
            vcm_log.close()
        self.vcm_mon = self.background.BackgroundTask(1, self.vcm_check)
        self.rxbb_vcm_timer = Timer(minutes*60, self.rxbb_vcm_stop)
        self.rxbb_vcm_timer.start()

    def rxbb_vcm_stop(self):
        self.vcm_mon.stop()


def cmd_hist_file(fname="eder.cmd"):
    import atexit
    import os
    import readline

    histfile = os.path.join(os.path.expanduser(""), fname)
    print '  Command history file: ' + histfile
    try:
        readline.read_history_file(histfile)
        readline.set_history_length(-1)
    except IOError:
        pass

    log_header = '**** New session started on ' + time.asctime()
    readline.add_history(log_header)

    atexit.register(readline.write_history_file, histfile)

def info_file(fname="eder.info"):
    import evk_logger
    return evk_logger.EvkLogger(fname)

def get_args():
    import argparse
    parser = argparse.ArgumentParser(description='Command line options.')
    parser.add_argument('-f', '--fref', dest='ref_freq', metavar='<freq>', default=None,
                         help='Specify reference clock frequency <freq>')
    parser.add_argument('--board', '-b', dest='evkplatform_type', choices=['MB0', 'MB1'], default='MB1',
                         help='Specify type of motherboard')
    parser.add_argument('--unit', '-u', dest='unit_name', metavar='UNIT', default=None,
                         help='Specify unit name')
    parser.add_argument('-r', '--rfm', dest='rfm_type', choices=['rfm_2.5','rfm_3.0'], default='rfm_3.0',
                         help='Specify type of RFM')
    return parser.parse_args()
    
if __name__ == "__main__":
    args = get_args()
    info_logger=info_file()
    cmd_hist_file()
    import rlcompleter, readline
    readline.parse_and_bind('tab:complete')
    import fileHandler as json

    
    if args.evkplatform_type == 'MB0':
        info_logger.log_info('Connecting to device connected to motherboard {0}.'.format(args.evkplatform_type),2)
        eder = Eder(log_instance=info_logger, evkplatform_type='MB0')
    elif args.evkplatform_type == 'MB1':
        try:
            info_logger.log_info("  Trying to import module mb1",2)
            import mb1
            if args.unit_name == None:
                dev_list = mb1.listdevs()
                dev_list = list(dict.fromkeys(dev_list))
                print
                for dev in dev_list:
                    print ('{}'.format(dev))
                print
                ser_num = raw_input('Enter EVK serial number from above list [SNxxxx]:')
            else:
                ser_num = args.unit_name
                
            if ser_num != '':
                print '  Connecting to device with unit name {0} connected to motherboard {1}.'.format(ser_num, args.evkplatform_type)
                eder = Eder(log_instance=info_logger, unit_name=ser_num, evkplatform_type=args.evkplatform_type, rfm_type=args.rfm_type, ref_freq=args.ref_freq)
        except ImportError as ie:
            info_logger.log_error("Error! " + str(ie),2)

Eder.connect_unit = cu.connect_unit

