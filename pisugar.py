
import smbus
import logging
import time
import threading
from datetime import datetime
import csv  # 导入 csv 模块
from collections import deque
# 设置 logging 级别为 INFO
# logging.basicConfig(level=logging.INFO)
logging.basicConfig(level=logging.DEBUG)

PiSugar_addresses = {
    "PiSugar2": 0x75,  # PiSugar2\2Plus
    "PiSugar3": 0x57,  # PiSugar3\3Plus
    "PiSugar2 RTC": 0x32  # PiSugar2\2Plus RTC
}
curve1200 = [
    (4.16, 100.0),
    (4.05, 95.0),
    (4.00, 80.0),
    (3.92, 65.0),
    (3.86, 40.0),
    (3.79, 25.5),
    (3.66, 10.0),
    (3.52, 6.5),
    (3.49, 3.2),
    (3.1, 0.0),
]
curve1200_3 = [
    (4.2, 100.0),  # 高电量阶段 (100%)
    (4.0, 80.0),   # 中电量阶段 (80%)
    (3.7, 60.0),   # 中电量阶段 (60%)
    (3.5, 20.0),   # 低电量阶段 (20%)
    (3.1, 0.0)     # 电量耗尽 (0%)
]
curve5000 = [
    (4.10, 100.0),
    (4.05, 95.0),
    (3.90, 88.0),
    (3.80, 77.0),
    (3.70, 65.0),
    (3.62, 55.0),
    (3.58, 49.0),
    (3.49, 25.6),
    (3.32, 4.5),
    (3.1, 0.0),
]


class PiSugarServer:
    def __init__(self):
        """
        PiSugar initialization, if unable to connect to any version of PiSugar, return false
        """
        self._bus = smbus.SMBus(1)
        self.ready = False
        self.modle = None
        self.i2creg = []
        self.address = 0
        self.battery_voltage = 0.00
        self.voltage_history = deque(maxlen=10)
        self.battery_level = 0
        self.battery_charging = 0
        self.temperature = 0
        self.power_plugged = False
        self.allow_charging = True
        self.lowpower_shutdown = False
        self.lowpower_shutdown_level = 10
        self.max_charge_voltage_protection = False
        self.max_protection_level=80
        # Start the device connection in a background thread
        self.connection_thread = threading.Thread(
            target=self._connect_device, daemon=True)
        self.connection_thread.start()

    def _connect_device(self):
        """
        Attempt to connect to the PiSugar device in a background thread.
        """
        while self.modle is None:
            if self.check_device(PiSugar_addresses["PiSugar2"]) is not None:
                self.address = PiSugar_addresses["PiSugar2"]
                if self.check_device(PiSugar_addresses["PiSugar2"], 0xC2) != 0:
                    self.modle = "PiSugar2Plus"
                else:
                    self.modle = "PiSugar2"
                self.device_init()
            elif self.check_device(PiSugar_addresses["PiSugar3"]) is not None:
                self.modle = 'PiSugar3'
                self.address = PiSugar_addresses["PiSugar3"]
                self.device_init()
            else:
                self.modle = None
                logging.info(
                    "No PiSugar device was found. Please check if the PiSugar device is powered on."
                )
                time.sleep(5)
        logging.info(f"{self.modle} is connected")
        # Once connected, start the timer
        self.start_timer()
        while len(self.i2creg) < 256:
            time.sleep(1)
        self.ready = True
        logging.info(f"{self.modle} is ready")

    def start_timer(self):

        # 创建一个线程来执行定时函数
        timer_thread = threading.Thread(target=self.update_value)
        timer_thread.daemon = True  # 设置为守护线程，主程序退出时自动结束
        timer_thread.start()

    def update_value(self):
        """每三秒更新pisugar状态，包括触发自动关机"""
        while True:
            try:
                if( self.modle == 'PiSugar2') | (self.modle == 'PiSugar2Plus'):
                    self.set_battery_notallow_charging() #短暂关闭充电以获取准确电池电压
                    time.sleep(0.05)
                self.i2creg = []
                for i in range(0, 256, 32):
                    # 计算当前读取的起始寄存器地址
                    current_register = 0 + i
                    # 计算当前读取的数据长度
                    current_length = min(32, 256 - i)
                    # 读取数据块
                    chunk = self._bus.read_i2c_block_data(
                        self.address, current_register, current_length)
                    # 将读取的数据块添加到结果列表中
                    self.i2creg.extend(chunk)
                    time.sleep(0.1)
                logging.debug(f"Data length: {len(self.i2creg)}")
                logging.debug(f"Data: {self.i2creg}")
                if self.modle == 'PiSugar3':
                    low = self.i2creg[0x23]
                    high = self.i2creg[0x22]
                    self.battery_voltage = (((high << 8) + low) / 1000)
                    self.temperature = self.i2creg[0x04]-40
                    ctr1 = self.i2creg[0x02]  # 读取控制寄存器 1
                    self.power_plugged = (ctr1 & (1 << 7)) != 0  # 检查电源是否插入
                    self.allow_charging = (ctr1 & (1 << 6)) != 0  # 检查是否允许充电
                    if self.max_charge_voltage_protection:
                        self._bus.write_byte_data(
                            self.address, 0x0B, 0x29)  # 关闭写保护
                        self._bus.write_byte_data(self.address, 0x20, self._bus.read_byte_data(
                            self.address, 0x20) | 0b10000000)
                        self._bus.write_byte_data(
                            self.address, 0x0B, 0x00)  # 开启写保护
                    else:
                        self._bus.write_byte_data(
                            self.address, 0x0B, 0x29)  # 关闭写保护
                        self._bus.write_byte_data(self.address, 0x20, self._bus.read_byte_data(
                            self.address, 0x20) & 0b01111111)
                        self._bus.write_byte_data(
                            self.address, 0x0B, 0x00)  # 开启写保护
                elif self.modle == 'PiSugar2':
                    high = self.i2creg[0xa3]
                    low = self.i2creg[0xa2]
                    self.battery_voltage = (2600.0 - (((high | 0b11000000) << 8) + low) * 0.26855) / \
                        1000.0 if high & 0x20 else (
                            2600.0 + (((high & 0x1f) << 8) + low) * 0.26855) / 1000.0
                    self.power_plugged = (self.i2creg[0x55] & 0b00010000) != 0

                    if self.max_charge_voltage_protection:
                        self.voltage_history.append(self.battery_voltage)
                        self.battery_level = self.convert_battery_voltage_to_level()
                        if (self.battery_level) > self.max_protection_level:
                            self.set_battery_notallow_charging()
                        else:
                            self.set_battery_allow_charging()
                    else:
                        self.set_battery_allow_charging()

                elif self.modle == 'PiSugar2Plus':
                    low = self.i2creg[0xd0]
                    high = self.i2creg[0xd1]
                    self.battery_voltage = (
                        (((high & 0b00111111) << 8) + low) * 0.26855 + 2600.0)/1000
                    self.power_plugged = self.i2creg[0xdd] == 0x1f
                    if self.max_charge_voltage_protection:
                        self.voltage_history.append(self.battery_voltage)
                        self.battery_level = self.convert_battery_voltage_to_level()
                        if (self.battery_level) > self.max_protection_level:
                            self.set_battery_notallow_charging()
                        else:
                            self.set_battery_allow_charging()
                    else:
                        self.set_battery_allow_charging()

                self.voltage_history.append(self.battery_voltage)
                self.battery_level = self.convert_battery_voltage_to_level()

                if self.lowpower_shutdown:
                    if self.battery_level < self.lowpower_shutdown_level:
                        logging.info("[PiSugarX] low power shutdown now.")
                        self.shutdown()

                time.sleep(3)
            except Exception as e:
                logging.error(f"read error{e}")
            time.sleep(3)

    def shutdown(self):
        # logging.info("[PiSugarX] PiSugar set shutdown .")
        if self.modle == 'PiSugar3':
            # 10秒后关闭电源
            self._bus.write_byte_data(self.address, 0x0B, 0x29)  # 关闭写保护
            self._bus.write_byte_data(self.address, 0x09, 10)
            self._bus.write_byte_data(self.address, 0x02, self._bus.read_byte_data(
                self.address, 0x02) & 0b11011111)
            self._bus.write_byte_data(self.address, 0x0B, 0x00)  # 开启写保护
            logging.info("[PiSugarX] PiSugar shutdown in 10s.")
        elif self.modle == 'PiSugar2':
            pass
        elif self.modle == 'PiSugar2Plus':
            pass

    def check_device(self, address, reg=0):
        """Check if a device is present at the specified address"""
        try:
            return self._bus.read_byte_data(address, reg)
        except OSError as e:
            logging.debug(f"Device not found at address {address}: {e}")
            return None

    def device_init(self):

        if self.modle == "PiSugar2Plus":
            '''初始化GPIO'''
            self._bus.write_byte_data(self.address, 0x52, self._bus.read_byte_data(
                self.address, 0x52) | 0b00000010)
            self._bus.write_byte_data(self.address, 0x54, self._bus.read_byte_data(
                self.address, 0x54) | 0b00000010)
            self._bus.write_byte_data(self.address, 0x52, self._bus.read_byte_data(
                self.address, 0x52) | 0b00000100)
            self._bus.write_byte_data(self.address, 0x29, self._bus.read_byte_data(
                self.address, 0x29) & 0b10111111)
            self._bus.write_byte_data(self.address, 0x52, self._bus.read_byte_data(
                self.address, 0x52) & 0b10011111 | 0b01000000)
            self._bus.write_byte_data(self.address, 0xc2, self._bus.read_byte_data(
                self.address, 0xc2) | 0b00010000)
            logging.debug(f"PiSugar2Plus GPIO 初始化完毕")
            '''Init boost intensity, 0x3f*50ma, 3A'''
            self._bus.write_byte_data(self.address, 0x30, self._bus.read_byte_data(
                self.address, 0x30) & 0b11000000 | 0x3f)
            logging.debug(f"PiSugar2Plus 电流设置完毕")

        elif self.modle == "PiSugar2":
            '''初始化GPIO'''
            self._bus.write_byte_data(self.address, 0x51, (self._bus.read_byte_data(
                self.address, 0x51) & 0b11110011) | 0b00000100)
            self._bus.write_byte_data(self.address, 0x53, self._bus.read_byte_data(
                self.address, 0x53) | 0b00000010)
            self._bus.write_byte_data(self.address, 0x51, (self._bus.read_byte_data(
                self.address, 0x51) & 0b11001111) | 0b00010000)
            self._bus.write_byte_data(self.address, 0x26, self._bus.read_byte_data(
                self.address, 0x26) & 0b10110000)
            self._bus.write_byte_data(self.address, 0x52, (self._bus.read_byte_data(
                self.address, 0x52) & 0b11110011) | 0b00000100)
            self._bus.write_byte_data(self.address, 0x53, (self._bus.read_byte_data(
                self.address, 0x53) & 0b11101111) | 0b00010000)
            logging.debug(f"PiSugar2 GPIO 初始化完毕")
        pass

    def convert_battery_voltage_to_level(self):
        """
        将电池电压转换为电量百分比。

        :param voltage: 当前电池电压
        :param curve: 电池阈值曲线，格式为 [(电压1, 电量1), (电压2, 电量2), ...]
        :return: 电量百分比
        """
        if (self.modle == "PiSugar2Plus") | (self.modle == "PiSugar3Plus"):
            curve = curve5000
        elif (self.modle == "PiSugar2"):
            curve = curve1200
        elif (self.modle == "PiSugar3"):
            curve = curve1200_3
         # 将当前电压加入历史记录

        # 如果历史记录不足 5 次，直接返回平均值（避免截尾后无有效数据）
        if len(self.voltage_history) < 5:
            avg_voltage = sum(self.voltage_history) / len(self.voltage_history)
        else:
            # 排序后去掉最高 2 个和最低 2 个
            sorted_history = sorted(self.voltage_history)
            trimmed_history = sorted_history[2:-2]  # 去掉前两个和后两个
            avg_voltage = sum(trimmed_history) / len(trimmed_history)  # 计算截尾平均
        # 遍历电池曲线的每一段
        for (v1, p1), (v2, p2) in zip(curve, curve[1:]):
            # 如果电压在当前区间内
            if v2 <= avg_voltage <= v1:
                # 使用线性插值计算电量
                return p2 + (p1 - p2) * (avg_voltage - v2) / (v1 - v2)

        # 如果电压超出曲线范围，返回最低或最高电量
        return curve[-1][1] if avg_voltage < curve[-1][0] else curve[0][1]

    def get_version(self):
        """
        Get the firmware version of the PiSugar3.
        If not PiSugar3, return None
        :return: Version string or None
        """
        if self.modle == 'PiSugar3':
            try:
                return bytes(self.i2creg[0xe2:0xee]).decode('ascii')
            except OSError as e:
                logging.error(f"Failed to read version from PiSugar3: {e}")
                return None
        return None

    def get_model(self):
        """
        Get the model of the PiSugar hardware.

        :return: Model string.
        """
        return self.modle

    def get_battery_level(self):
        """
        Get the current battery level in percentage.

        :return: Battery level as a percentage (0-100).
        """
        return self.battery_level

    def get_battery_voltage(self):
        """
        Get the current battery voltage.

        :return: Battery voltage in volts.
        """
        return self.battery_voltage

    def get_battery_current(self):
        """
        Get the current battery current.

        :return: Battery current in amperes.
        """
        pass

    def get_battery_allow_charging(self):
        """
        Check if battery charging is allowed.

        :return: True if charging is allowed, False otherwise.
        """
        return self.allow_charging

    def set_battery_allow_charging(self):
        if self.modle == 'PiSugar3':
            pass
        elif self.modle == 'PiSugar2':
            # 禁止 gpio2 输出
            self._bus.write_byte_data(self.address, 0x54, self._bus.read_byte_data(
                self.address, 0x54) & 0b11111011)
            # 开启充电
            self._bus.write_byte_data(self.address, 0x55, self._bus.read_byte_data(
                self.address, 0x55) & 0b11111011)
            # 开启 gpio2 输出
            self._bus.write_byte_data(self.address, 0x54, self._bus.read_byte_data(
                self.address, 0x54) | 0b00000100)
        elif self.modle == 'PiSugar2Plus':
            # 禁止 gpio2 输出
            self._bus.write_byte_data(self.address, 0x56, self._bus.read_byte_data(
                self.address, 0x56) & 0b11111011)
            # 开启充电
            self._bus.write_byte_data(self.address, 0x58, self._bus.read_byte_data(
                self.address, 0x58) & 0b11111011)
            # 开启 gpio2 输出
            self._bus.write_byte_data(self.address, 0x56, self._bus.read_byte_data(
                self.address, 0x56) | 0b00000100)

        return

    def set_battery_notallow_charging(self):
        if self.modle == 'PiSugar3':
            pass
        elif self.modle == 'PiSugar2':
            # 禁止 gpio2 输出
            print(self._bus.write_byte_data(self.address, 0x54, self._bus.read_byte_data(
                self.address, 0x54) & 0b11111011))
            # 关闭充电
            self._bus.write_byte_data(self.address, 0x55, self._bus.read_byte_data(
                self.address, 0x55) | 0b00000100)
            # 开启 gpio2 输出
            self._bus.write_byte_data(self.address, 0x54, self._bus.read_byte_data(
                self.address, 0x54) | 0b00000100)
        elif self.modle == 'PiSugar2Plus':
            # 禁止 gpio2 输出
            self._bus.write_byte_data(self.address, 0x56, self._bus.read_byte_data(
                self.address, 0x56) & 0b11111011)
            # 关闭充电
            self._bus.write_byte_data(self.address, 0x58, self._bus.read_byte_data(
                self.address, 0x58) | 0b00000100)
            # 开启 gpio2 输出
            self._bus.write_byte_data(self.address, 0x56, self._bus.read_byte_data(
                self.address, 0x56) | 0b00000100)

        return

    def get_battery_charging_range(self):
        """
        Get the battery charging range.

        :return: Charging range string.
        """
        pass

    def get_battery_full_charge_duration(self):
        """
        Get the duration of keeping the battery charging when full.

        :return: Duration in seconds.
        """
        pass

    def get_battery_safe_shutdown_level(self):
        """
        Get the safe shutdown level for the battery.

        :return: Safe shutdown level as a percentage.
        """
        pass

    def get_battery_safe_shutdown_delay(self):
        """
        Get the safe shutdown delay.

        :return: Delay in seconds.
        """
        pass

    def get_battery_auto_power_on(self):
        """
        Check if auto power on is enabled.

        :return: True if enabled, False otherwise.
        """
        pass

    def get_battery_soft_poweroff(self):
        """
        Check if soft power off is enabled.

        :return: True if enabled, False otherwise.
        """
        pass

    def get_system_time(self):
        """
        Get the system time.

        :return: System time string.
        """
        pass

    def get_rtc_adjust_ppm(self):
        """
        Get the RTC adjust PPM.

        :return: RTC adjust PPM value.
        """
        pass

    def get_rtc_alarm_repeat(self):
        """
        Get the RTC alarm repeat setting.

        :return: RTC alarm repeat string.
        """
        pass

    def get_tap_enable(self, tap):
        """
        Check if a specific tap (single, double, long) is enabled.

        :param tap: Type of tap ('single', 'double', 'long').
        :return: True if enabled, False otherwise.
        """
        pass

    def get_tap_shell(self, tap):
        """
        Get the shell command associated with a specific tap.

        :param tap: Type of tap ('single', 'double', 'long').
        :return: Shell command string.
        """
        pass

    def get_anti_mistouch(self):
        """
        Check if anti-mistouch protection is enabled.

        :return: True if enabled, False otherwise.
        """
        pass

    def get_temperature(self):
        """
        Get the current temperature.

        :return: Temperature in degrees Celsius.
        """
        return self.temperature

    def get_battery_power_plugged(self):
        """
        Check if the battery is plugged in.

        :return: True if plugged in, False otherwise.
        """
        return self.power_plugged

    def get_battery_charging(self):
        """
        Check if the battery is currently charging.

        :return: True if charging, False otherwise.
        """
        pass

    def rtc_web(self):
        """
        Synchronize RTC with web time.
        """
        pass


# Example usage
if __name__ == "__main__":

    pi_sugar = PiSugarServer()
    pi_sugar.max_charge_voltage_protection = True
 # 打开一个 CSV 文件用于追加写入
    with open("pi_sugar_output.csv", "a", newline="") as file:
        writer = csv.writer(file)

        # 如果是新文件，写入表头
        if file.tell() == 0:
            writer.writerow(["Time", "Model", "Plugged", "Voltage", "Level"])

        while True:
            # 获取当前时间并格式化为字符串
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # 将数据写入 CSV 文件
            writer.writerow([
                current_time,
                pi_sugar.get_model(),
                pi_sugar.get_battery_power_plugged(),
                pi_sugar.get_battery_voltage(),
                pi_sugar.get_battery_level()
            ])
            print(
                f"Time {current_time} Voltage {pi_sugar.get_battery_voltage()} Level {pi_sugar.get_battery_level()}")

            # 刷新文件缓冲区，确保数据写入文件
            file.flush()
            time.sleep(1)
