"""
Модуль для управления настройками из файла
"""

import os
import logging


class ConfigManager:
    def __init__(self, config_file='settings.txt'):
        """
        Инициализация менеджера конфигурации

        Args:
            config_file: путь к файлу с настройками
        """
        self.config_file = config_file
        self.deposit = 9196
        self.risk_percent = 0.25
        self.leverage = 5
        self.risk_reward_ratio = 1.0

        # Загружаем настройки при инициализации
        self.load_config()

    def load_config(self):
        """
        Загрузка настроек из файла
        """
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as file:
                    for line in file:
                        line = line.strip()
                        if not line or line.startswith('#'):
                            continue

                        if 'deposit' in line.lower():
                            # Извлекаем число из строки типа "deposit- 9180" или "deposit - 9180"
                            value = line.split('-')[-1].strip() if '-' in line else line.split()[-1]
                            self.deposit = float(value)

                        elif 'risk' in line.lower():
                            value = line.split('-')[-1].strip() if '-' in line else line.split()[-1]
                            self.risk_percent = float(value)

                        elif 'leverage' in line.lower():
                            value = line.split('-')[-1].strip() if '-' in line else line.split()[-1]
                            self.leverage = int(float(value))

                        elif 'tp/sl' in line.lower() or 'tp_sl' in line.lower() or 'rr' in line.lower():
                            value = line.split('-')[-1].strip() if '-' in line else line.split()[-1]
                            # Парсим формат "1:1" или "1.5"
                            if ':' in value:
                                ratio_parts = value.split(':')
                                self.risk_reward_ratio = float(ratio_parts[0]) / float(ratio_parts[1])
                            else:
                                self.risk_reward_ratio = float(value)

                logging.info(f"Настройки загружены из {self.config_file}: "
                             f"deposit={self.deposit}, risk={self.risk_percent}%, "
                             f"leverage={self.leverage}x, RR={self.risk_reward_ratio}:1")
                return True
            else:
                # Создаем файл с настройками по умолчанию
                self.save_config()
                logging.info(f"Создан файл конфигурации {self.config_file} со значениями по умолчанию")
                return True

        except Exception as e:
            logging.error(f"Ошибка загрузки конфигурации: {e}")
            return False

    def save_config(self):
        """
        Сохранение настроек в файл
        """
        try:
            with open(self.config_file, 'w', encoding='utf-8') as file:
                file.write(f"deposit - {self.deposit}\n")
                file.write(f"risk - {self.risk_percent}\n")
                file.write(f"leverage - {self.leverage}\n")
                file.write(f"TP/SL - {self.risk_reward_ratio}:1\n")

            logging.info(f"Настройки сохранены в {self.config_file}")
            return True
        except Exception as e:
            logging.error(f"Ошибка сохранения конфигурации: {e}")
            return False

    def update_setting(self, setting_name, value):
        """
        Обновление конкретной настройки

        Args:
            setting_name: имя настройки ('deposit', 'risk', 'leverage', 'rr')
            value: новое значение
        """
        try:
            if setting_name == 'deposit':
                self.deposit = float(value)
            elif setting_name == 'risk':
                self.risk_percent = float(value)
            elif setting_name == 'leverage':
                self.leverage = int(float(value))
            elif setting_name == 'rr':
                self.risk_reward_ratio = float(value)
            else:
                return False

            # Сохраняем обновленные настройки в файл
            self.save_config()
            return True
        except Exception as e:
            logging.error(f"Ошибка обновления настройки {setting_name}: {e}")
            return False

    def get_settings_text(self):
        """
        Получение текстового представления настроек

        Returns:
            str: форматированный текст настроек
        """
        rr_text = f"{self.risk_reward_ratio}:1" if self.risk_reward_ratio >= 1 else f"1:{int(1 / self.risk_reward_ratio)}"

        settings_text = f"⚙️ ТЕКУЩИЕ НАСТРОЙКИ\n"
        settings_text += f"├─ Депозит: ${self.deposit} USDT\n"
        settings_text += f"├─ Риск на сделку: {self.risk_percent}%\n"
        settings_text += f"├─ Сумма риска: ${self.deposit * self.risk_percent / 100:.2f} USDT\n"
        settings_text += f"├─ Кредитное плечо: {self.leverage}x\n"
        settings_text += f"└─ Соотношение R:R: {rr_text}\n\n"
        settings_text += f"💡 Команды для изменения:\n"
        settings_text += f"   set deposit 10000 - изменить депозит\n"
        settings_text += f"   set risk 0.5 - изменить риск (%)\n"
        settings_text += f"   set leverage 10 - изменить плечо\n"
        settings_text += f"   set rr 2 - изменить RR (1:2)\n"
        settings_text += f"   reload config - перезагрузить настройки из файла"

        return settings_text
