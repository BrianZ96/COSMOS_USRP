//
// Copyright 2010-2012,2014 Ettus Research LLC
// Copyright 2018 Ettus Research, a National Instruments Company
//
// SPDX-License-Identifier: GPL-3.0-or-later
//

#include "wavetable2.hpp"
#include <uhd/exception.hpp>
#include <uhd/usrp/multi_usrp.hpp>
#include <uhd/utils/safe_main.hpp>
#include <uhd/utils/static.hpp>
#include <uhd/utils/thread.hpp>
#include <stdint.h>
#include <boost/algorithm/string.hpp>
#include <boost/format.hpp>
#include <boost/math/special_functions/round.hpp>
#include <boost/program_options.hpp>
#include <chrono>
#include <csignal>
#include <iostream>
#include <string>
#include <thread>
#include <fstream>

namespace po = boost::program_options;

/***********************************************************************
 * Signal handlers
 **********************************************************************/
static bool stop_signal_called = false;
void sig_int_handler(int)
{
    stop_signal_called = true;
}

/***********************************************************************
 * Main function
 **********************************************************************/
int UHD_SAFE_MAIN(int argc, char* argv[])
{
    	uhd::set_thread_priority_safe();

    // variables to be set by po
    std::string args, wave_type, ant, subdev, ref, pps, otw, channel_list;
    uint64_t total_num_samps;
    size_t spb;
    double rate, freq, gain, wave_freq, bw, lo_offset, stepSize;
    float ampl;

//    ifstream reader("MIMO_Drone_50MS_50MHz_SIMO_5usspace.txt");
//    ifstream reader("Drone_signal_50MS_50MHz_20KHz.txt");
//    ifstream reader("MIMO_Drone_signal_100MS_100MHz_SIMO_signal500us.txt");
//    ifstream reader("MIMO_Drone_100MS_100MHz_SIMO_500usv2space.txt");
//     ifstream reader("MIMO_Drone_50MS_50MHz_SIMO_500usspacev2.txt");
     ifstream reader("MIMO_Drone_100MS_100MHz_SISO_5us.txt");




    // setup the program options
    po::options_description desc("Allowed options");
    // clang-format off
    desc.add_options()
        ("help", "help message")
        ("args", po::value<std::string>(&args)->default_value(""), "single uhd device address args")
        ("spb", po::value<size_t>(&spb)->default_value(0), "samples per buffer, 0 for default")
        ("nsamps", po::value<uint64_t>(&total_num_samps)->default_value(0), "total number of samples to transmit")
        ("rate", po::value<double>(&rate), "rate of outgoing samples")
        ("freq", po::value<double>(&freq), "RF center frequency in Hz")
        ("lo-offset", po::value<double>(&lo_offset)->default_value(0.0),
            "Offset for frontend LO in Hz (optional)")
        ("ampl", po::value<float>(&ampl)->default_value(float(0.7)), "amplitude of the waveform [0 to 0.7]")
        ("gain", po::value<double>(&gain), "gain for the RF chain")
        ("ant", po::value<std::string>(&ant), "antenna selection")
        ("subdev", po::value<std::string>(&subdev), "subdevice specification")
        ("bw", po::value<double>(&bw), "analog frontend filter bandwidth in Hz")
        ("wave-type", po::value<std::string>(&wave_type)->default_value("OFDM"), "waveform type (CONST, SQUARE, RAMP, SINE)")
        ("wave-freq", po::value<double>(&wave_freq)->default_value(0), "waveform frequency in Hz")
        ("ref", po::value<std::string>(&ref)->default_value("external"), "clock reference (internal, external, mimo, gpsdo)")
        ("pps", po::value<std::string>(&pps)->default_value("external"), "PPS source (internal, external, mimo, gpsdo)")
        ("otw", po::value<std::string>(&otw)->default_value("sc16"), "specify the over-the-wire sample mode")
        ("channels", po::value<std::string>(&channel_list)->default_value("0"), "which channels to use (specify \"0\", \"1\", \"0,1\", etc)")
        ("int-n", "tune USRP with integer-N tuning")
    ;
    // clang-format on
    po::variables_map vm;
    po::store(po::parse_command_line(argc, argv, desc), vm);
    po::notify(vm);

    // print the help message
    if (vm.count("help")) {
        std::cout << boost::format("UHD TX Waveforms %s") % desc << std::endl;
        return ~0;
    }

    // create a usrp device
    std::cout << std::endl;
    std::cout << boost::format("Creating the usrp device with: %s...") % args
              << std::endl;
    uhd::usrp::multi_usrp::sptr usrp = uhd::usrp::multi_usrp::make(args);

    // always select the subdevice first, the channel mapping affects the other settings
    if (vm.count("subdev"))
        usrp->set_tx_subdev_spec(subdev);

    // detect which channels to use
    std::vector<std::string> channel_strings;
    std::vector<size_t> channel_nums;
    boost::split(channel_strings, channel_list, boost::is_any_of("\"',"));
    for (size_t ch = 0; ch < channel_strings.size(); ch++) {
        size_t chan = std::stoi(channel_strings[ch]);
        if (chan >= usrp->get_tx_num_channels())
            throw std::runtime_error("Invalid channel(s) specified.");
        else
            channel_nums.push_back(std::stoi(channel_strings[ch]));
    }

    // Lock mboard clocks
    usrp->set_clock_source(ref);

    std::cout << boost::format("Using Device: %s") % usrp->get_pp_string() << std::endl;

    // set the sample rate
    if (not vm.count("rate")) {
        std::cerr << "Please specify the sample rate with --rate" << std::endl;
        return ~0;
    }
    std::cout << boost::format("Setting TX Rate: %f Msps...") % (rate / 1e6) << std::endl;
    usrp->set_tx_rate(rate);
    std::cout << boost::format("Actual TX Rate: %f Msps...") % (usrp->get_tx_rate() / 1e6)
              << std::endl
              << std::endl;

    // set the center frequency
    if (not vm.count("freq")) {
        std::cerr << "Please specify the center frequency with --freq" << std::endl;
        return ~0;
    }

    for (size_t ch = 0; ch < channel_nums.size(); ch++) {
        std::cout << boost::format("Setting TX Freq: %f MHz...") % (freq / 1e6)
                  << std::endl;
        std::cout << boost::format("Setting TX LO Offset: %f MHz...") % (lo_offset / 1e6)
                  << std::endl;
        uhd::tune_request_t tune_request(freq, lo_offset);
        if (vm.count("int-n"))
            tune_request.args = uhd::device_addr_t("mode_n=integer");
        usrp->set_tx_freq(tune_request, channel_nums[ch]);
        std::cout << boost::format("Actual TX Freq: %f MHz...")
                         % (usrp->get_tx_freq(channel_nums[ch]) / 1e6)
                  << std::endl
                  << std::endl;

        // set the rf gain
        if (vm.count("gain")) {
            std::cout << boost::format("Setting TX Gain: %f dB...") % gain << std::endl;
            usrp->set_tx_gain(gain, channel_nums[ch]);
            std::cout << boost::format("Actual TX Gain: %f dB...")
                             % usrp->get_tx_gain(channel_nums[ch])
                      << std::endl
                      << std::endl;
        }

        // set the analog frontend filter bandwidth
        if (vm.count("bw")) {
            std::cout << boost::format("Setting TX Bandwidth: %f MHz...") % bw
                      << std::endl;
            usrp->set_tx_bandwidth(bw, channel_nums[ch]);
            std::cout << boost::format("Actual TX Bandwidth: %f MHz...")
                             % usrp->get_tx_bandwidth(channel_nums[ch])
                      << std::endl
                      << std::endl;
        }

        // set the antenna
        if (vm.count("ant"))
            usrp->set_tx_antenna(ant, channel_nums[ch]);
    }

    std::this_thread::sleep_for(std::chrono::seconds(1)); // allow for some setup time

    // for the const wave, set the wave freq for small samples per period
    if (wave_freq == 0) {
        if (wave_type == "CONST") {
            wave_freq = usrp->get_tx_rate() / 2;
        } else {
            throw std::runtime_error(
                "wave freq cannot be 0 with wave type other than CONST");
        }
    }

    // error when the waveform is not possible to generate
    if (std::abs(wave_freq) > usrp->get_tx_rate() / 2) {
        throw std::runtime_error("wave freq out of Nyquist zone");
    }
    if (usrp->get_tx_rate() / std::abs(wave_freq) > wave_table_len / 2) {
        throw std::runtime_error("wave freq too small for table");
    }

    // pre-compute the waveform values
    const wave_table_class wave_table(wave_type, ampl);
    std::cout<<"wave_table_len = "<<wave_table_len<<std::endl;

    //std::cout << "please enter stepsize" << endl;
    //std::cin  >> stepSize;

    // for 2500 for 40 MHz sampling rate
    //stepSize = .786432;

    // for 2500 for 50 MHz sampling rate
    //stepSize = .98304;
    //std::cout << "stepSize fixed @ "<<stepSize<<std::endl;

    // for 8192 @ 40
    //stepSize = .24


    // for 8192 @ 50
    //stepSize = .3;

    // sum always ends up being 491.52 rounded
    //const size_t step =
      //  boost::math::iround(wave_freq / usrp->get_tx_rate() * wave_table_len*(stepSize));
    size_t index = 0;


    // fixing step @ 491.52 with iround
    const size_t step = 493;// 493 is best
    std::cout<<"Fixing step function @ "<<step<<std::endl;
    int rand=0;
    //std::cin >> rand;

    // create a transmit streamer
    // linearly map channels (index0 = channel0, index1 = channel1, ...)
    uhd::stream_args_t stream_args("fc32", otw);
    stream_args.channels             = channel_nums;
    uhd::tx_streamer::sptr tx_stream = usrp->get_tx_stream(stream_args);

    // allocate a buffer which we re-use for each channel
    if (spb == 0) {
        spb = tx_stream->get_max_num_samps() * 10; // multiplier is by default 10, try it with 100. Buffer by default is limited to 9960 samples our signal is 25k
    }
    std::vector<std::complex<float>> buff(spb);
    std::vector<std::complex<float>*> buffs(channel_nums.size(), &buff.front());

    // pre-fill the buffer with the waveform
    std::cout<<"size of buffer "<<buff.size()<<" and step " << step << std::endl;
    //std::cin >> rand;
    for (size_t n = 0; n < buff.size(); n++) {
        buff[n] = wave_table(index += step);
        //std::cout<< n <<":"  << buff[n] << endl;  // buffer containing waveform
    }

    index = 0;
    for (size_t nn = 0; nn < 300; nn++) {
        std::cout<<wave_table(index += step)<<std::endl;
        //std::cout<< n <<":"  << buff[n] << endl;  // buffer containing waveform
    }



    std::cout << boost::format("Setting device timestamp to 0...") << std::endl;
    std::cout << "channel num = " << channel_nums.size()<< std::endl;
    if (channel_nums.size() >= 1) {
        // Sync times
        if (pps == "mimo") {
            UHD_ASSERT_THROW(usrp->get_num_mboards() == 2);

            // make mboard 1 a slave over the MIMO Cable
            usrp->set_time_source("mimo", 1);

            // set time on the master (mboard 0)
            usrp->set_time_now(uhd::time_spec_t(0.0), 0);

            // sleep a bit while the slave locks its time to the master
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        } else {
            if (pps == "internal" or pps == "external" or pps == "gpsdo"){
                usrp->set_time_source(pps);
                std::cout<<"pps set success"<<std::endl;
            }
            usrp->set_time_unknown_pps(uhd::time_spec_t(0.0));
            std::this_thread::sleep_for(
                std::chrono::seconds(1)); // wait for pps sync pulse
        }
    } else {
        usrp->set_time_now(0.0);
    }

    // Check Ref and LO Lock detect
    std::vector<std::string> sensor_names;
    const size_t tx_sensor_chan = channel_nums.empty() ? 0 : channel_nums[0];
    sensor_names                = usrp->get_tx_sensor_names(tx_sensor_chan);
    if (std::find(sensor_names.begin(), sensor_names.end(), "lo_locked")
        != sensor_names.end()) {
        uhd::sensor_value_t lo_locked = usrp->get_tx_sensor("lo_locked", tx_sensor_chan);
        std::cout << boost::format("Checking TX: %s ...") % lo_locked.to_pp_string()
                  << std::endl;
        UHD_ASSERT_THROW(lo_locked.to_bool());
    }
    const size_t mboard_sensor_idx = 0;
    sensor_names                   = usrp->get_mboard_sensor_names(mboard_sensor_idx);
    if ((ref == "mimo")
        and (std::find(sensor_names.begin(), sensor_names.end(), "mimo_locked")
                != sensor_names.end())) {
        uhd::sensor_value_t mimo_locked =
            usrp->get_mboard_sensor("mimo_locked", mboard_sensor_idx);
        std::cout << boost::format("Checking TX: %s ...") % mimo_locked.to_pp_string()
                  << std::endl;
        UHD_ASSERT_THROW(mimo_locked.to_bool());
    }
    if ((ref == "external")
        and (std::find(sensor_names.begin(), sensor_names.end(), "ref_locked")
                != sensor_names.end())) {
        uhd::sensor_value_t ref_locked =
            usrp->get_mboard_sensor("ref_locked", mboard_sensor_idx);
        std::cout << boost::format("Checking TX: %s ...") % ref_locked.to_pp_string()
                  << std::endl;
        UHD_ASSERT_THROW(ref_locked.to_bool());
    }


    std::signal(SIGINT, &sig_int_handler);
    std::cout << "Press Ctrl + C to stop streaming...\n\npress any key to continue" << std::endl;


    // DEBUG
    rand=0;
    //std::cin >> rand;

    // /////////////////////////////
    // Streaming occurs here
    // /////////////////////////////

    // Set up metadata. We start streaming a bit in the future
    // to allow MIMO operation:
    uhd::tx_metadata_t md;
    md.start_of_burst = true;
    md.end_of_burst   = false;
    md.has_time_spec  = true;
    md.time_spec      = usrp->get_time_now() + uhd::time_spec_t(0.1);
	//md.time_spec      = usrp->get_time_now();
    // send data until the signal handler gets called
    // or if we accumulate the number of samples specified (unless it's 0)
    uint64_t num_acc_samps = 0;

    // ///////////////////////////////
    // Us are being produced here
    // ///////////////////////////////
        uhd::set_thread_priority(); //Added as suggested by Neel
    while (true) {
        // Break on the end of duration or CTRL-C
        if (stop_signal_called) {
            break;
        }
        // Break when we've received nsamps
        if (total_num_samps > 0 and num_acc_samps >= total_num_samps) {
            break;
        }

        // send the entire contents of the buffer
        num_acc_samps += tx_stream->send(buffs, buff.size(), md);
	
	// fill the buffer with the waveform (Added this part from old code)
        for (size_t n = 0; n < buff.size(); n++) {
            buff[n] = wave_table(index += step);
        }

        md.start_of_burst = false;
        md.has_time_spec  = false;
    }

    // send a mini EOB packet
    md.end_of_burst = true;
    tx_stream->send("", 0, md);

    // finished
    std::cout << std::endl << "Done!" << std::endl << std::endl;
    return EXIT_SUCCESS;

}
