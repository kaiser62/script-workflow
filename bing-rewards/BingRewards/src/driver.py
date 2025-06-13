from abc import ABC, abstractmethod
import os
import platform
import ssl
import zipfile
import shutil
import re
import random
import string
from urllib.request import urlopen

from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.support.abstract_event_listener import AbstractEventListener
from selenium.webdriver.support.event_firing_webdriver import EventFiringWebDriver
from selenium.common.exceptions import SessionNotCreatedException, WebDriverException


class EventListener(AbstractEventListener):
    """Attempt to disable animations"""
    def after_click(self, url, driver):
        animation = r"try { jQuery.fx.off = true; } catch(e) {}"
        driver.execute_script(animation)


class Driver(EventFiringWebDriver):
    def __init__(self, driver, event_listener, device):
        super().__init__(driver, event_listener)
        self.device = device

    def close_other_tabs(self):
        curr = self.current_window_handle
        for handle in self.window_handles:
            self.switch_to.window(handle)
            if handle != curr:
                self.close()
        self.switch_to.window(curr)

    def switch_to_n_tab(self, n):
        self.switch_to.window(self.window_handles[n])

    def switch_to_first_tab(self):
        self.switch_to_n_tab(0)

    def switch_to_last_tab(self):
        self.switch_to_n_tab(-1)


class DriverFactory(ABC):
    WEB_DEVICE = 'web'
    MOBILE_DEVICE = 'mobile'
    DRIVERS_DIR = "drivers"

    __WEB_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/105.0.0.0 Safari/537.36 Edg/105.0.1343.33"
    __MOBILE_USER_AGENT = "Mozilla/5.0 (Linux; Android 10; HD1913) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/105.0.5195.79 Mobile Safari/537.36 EdgA/100.0.1185.50"

    @property
    @staticmethod
    @abstractmethod
    def VERSION_MISMATCH_STR():
        pass

    @property
    @staticmethod
    @abstractmethod
    def WebDriverCls():
        pass

    @property
    @staticmethod
    @abstractmethod
    def WebDriverOptions():
        pass

    @property
    @staticmethod
    @abstractmethod
    def driver_name():
        pass

    @staticmethod
    @abstractmethod
    def _get_latest_driver_url(dl_try_count):
        raise NotImplementedError

    @classmethod
    def __download_driver(cls, dl_try_count=0):
        url = cls._get_latest_driver_url(dl_try_count)
        response = urlopen(url, context=ssl.SSLContext(ssl.PROTOCOL_TLS))
        zip_file_path = os.path.join(cls.DRIVERS_DIR, os.path.basename(url))

        with open(zip_file_path, 'wb') as zip_file:
            while chunk := response.read(1024):
                zip_file.write(chunk)

        extracted_dir = os.path.splitext(zip_file_path)[0]
        with zipfile.ZipFile(zip_file_path, "r") as zip_file:
            zip_file.extractall(extracted_dir)
        os.remove(zip_file_path)

        driver_path = os.path.join(cls.DRIVERS_DIR, cls.driver_name)
        try:
            os.rename(os.path.join(extracted_dir, cls.driver_name), driver_path)
        except FileExistsError:
            os.replace(os.path.join(extracted_dir, cls.driver_name), driver_path)

        shutil.rmtree(extracted_dir)
        os.chmod(driver_path, 0o755)

    @classmethod
    def add_driver_options(cls, device, headless, cookies, nosandbox):
        options = cls.WebDriverOptions()
        options.add_argument("--disable-extensions")
        options.add_argument("--window-size=1280,1024")
        options.add_argument("--log-level=3")
        options.add_argument("--disable-notifications")
        options.add_argument("disable-infobars")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-dev-shm-usage")

        options.add_experimental_option("prefs", {
            "profile.default_content_setting_values.geolocation": 1,
            "profile.default_content_setting_values.notifications": 2,
            "profile.default_content_setting_values.images": 2
        })

        if headless:
            options.add_argument("--headless=new")

        user_agent = cls.__WEB_USER_AGENT if device == cls.WEB_DEVICE else cls.__MOBILE_USER_AGENT
        options.add_argument("user-agent=" + user_agent)

        if cookies:
            cookies_path = os.path.join(os.getcwd(), 'stored_browser_data/')
            options.add_argument("user-data-dir=" + cookies_path)

        if nosandbox:
            options.add_argument("--no-sandbox")

        return options

    @classmethod
    def get_driver(cls, device, headless, cookies, nosandbox) -> Driver:
        dl_try_count = 0
        MAX_TRIES = 4
        is_dl_success = False
        options = cls.add_driver_options(device, headless, cookies, nosandbox)

        if platform.machine() in ["armv7l", "aarch64"]:
            driver_path = "/usr/lib/chromium-browser/chromedriver"
        else:
            if not os.path.exists(cls.DRIVERS_DIR):
                os.mkdir(cls.DRIVERS_DIR)
            driver_path = os.path.join(cls.DRIVERS_DIR, cls.driver_name)
            if not os.path.exists(driver_path):
                cls.__download_driver()
                dl_try_count += 1

        service_cls = ChromeService if cls.WebDriverCls == webdriver.Chrome else EdgeService
        service = service_cls(executable_path=driver_path)

        while not is_dl_success:
            try:
                driver = cls.WebDriverCls(service=service, options=options)
                is_dl_success = True
            except SessionNotCreatedException as se:
                if cls.VERSION_MISMATCH_STR in str(se).lower():
                    print('The downloaded driver does not match browser version...\n')
                    if dl_try_count == MAX_TRIES:
                        raise SessionNotCreatedException(
                            f"Tried downloading the {dl_try_count} most recent drivers. None matched your browser version."
                        )
                    cls.__download_driver(dl_try_count)
                    dl_try_count += 1
                else:
                    raise
            except WebDriverException as wde:
                if "DevToolsActivePort file doesn't exist" in str(wde):
                    options = cls.add_driver_options(device, headless, cookies=False, nosandbox=nosandbox)
                else:
                    raise

        return Driver(driver, EventListener(), device)


class ChromeDriverFactory(DriverFactory):
    WebDriverCls = webdriver.Chrome
    WebDriverOptions = webdriver.ChromeOptions
    VERSION_MISMATCH_STR = 'this version of chromedriver only supports chrome version'
    driver_name = "chromedriver.exe" if platform.system() == "Windows" else "chromedriver"

    @staticmethod
    def _get_latest_driver_url(dl_try_count):
        CHROME_RELEASE_URL = "https://sites.google.com/chromium.org/driver/downloads?authuser=0"
        response = urlopen(CHROME_RELEASE_URL, context=ssl.SSLContext(ssl.PROTOCOL_TLS)).read()
        latest_version = re.findall(rb"ChromeDriver \d{2,3}\.0\.\d{4}\.\d+", response)[dl_try_count].decode().split()[1]
        print(f'Downloading {platform.system()} chromedriver version: {latest_version}')

        system = platform.system()
        if system == "Windows":
            return f"https://chromedriver.storage.googleapis.com/{latest_version}/chromedriver_win32.zip"
        if system == "Darwin":
            return f"https://chromedriver.storage.googleapis.com/{latest_version}/chromedriver_mac_arm64.zip" if platform.processor() == 'arm' else f"https://chromedriver.storage.googleapis.com/{latest_version}/chromedriver_mac64.zip"
        return f"https://chromedriver.storage.googleapis.com/{latest_version}/chromedriver_linux64.zip"


class MsEdgeDriverFactory(DriverFactory):
    WebDriverCls = webdriver.Edge
    WebDriverOptions = webdriver.EdgeOptions
    VERSION_MISMATCH_STR = 'this version of microsoft edge webdriver only supports microsoft edge version'
    driver_name = "msedgedriver.exe" if platform.system() == "Windows" else "msedgedriver"

    @staticmethod
    def _get_latest_driver_url(dl_try_count):
        EDGE_RELEASE_URL = "https://developer.microsoft.com/en-us/microsoft-edge/tools/webdriver/"
        response = urlopen(EDGE_RELEASE_URL, context=ssl.SSLContext(ssl.PROTOCOL_TLS)).read()
        latest_version = re.findall(rb"Version: \d{2,3}\.0\.\d{4}\.\d+", response)[dl_try_count].decode().split()[1]
        print(f'Downloading {platform.system()} msedgedriver version: {latest_version}')

        system = platform.system()
        if system == "Windows":
            return f"https://msedgedriver.azureedge.net/{latest_version}/edgedriver_win64.zip"
        if system == "Darwin":
            return f"https://msedgedriver.azureedge.net/{latest_version}/edgedriver_mac64.zip"
        return f"https://msedgedriver.azureedge.net/{latest_version}/edgedriver_linux64.zip"
