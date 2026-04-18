"""
Главный VK бот - оркестратор
Управляет парсером биржи и калькулятором позиций
"""

import vk_api
import random
import time
import threading
import logging
from datetime import datetime
from vk_api.longpoll import VkLongPoll, VkEventType
import VKtoken
from exchange_parser import ExchangeParser
from position_calculator import PositionCalculator
from config_manager import ConfigManager

# Конфигурация
CONFIG_FILE = 'settings.txt'  # Файл с настройками
RECIPIENT_ID = 473616188  # ID получателя сообщений
COINS_FILE = 'Coins.txt'
CHECK_INTERVAL = 50  # Интервал проверки в секундах

# Настройка логирования
logging.basicConfig(
    filename='py_log.log',
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)


class VKTraderBot:
    def __init__(self, token, recipient_id, config_file='settings.txt'):
        """
        Инициализация главного бота

        Args:
            token: токен VK API
            recipient_id: ID получателя сообщений
            config_file: путь к файлу с настройками
        """
        self.token = token
        self.recipient_id = recipient_id

        # Инициализация менеджера конфигурации
        self.config_manager = ConfigManager(config_file)

        # Инициализация модулей
        self.exchange = ExchangeParser(use_spot=True)
        self.calculator = PositionCalculator(self.config_manager)

        # Инициализация VK
        self.vk_session = vk_api.VkApi(token=token)
        self.vk = self.vk_session.get_api()
        self.longpoll = VkLongPoll(self.vk_session)

        # Состояния бота
        self.waiting_for_target = None  # {symbol: str, trade_type: str, rsi: float} - только один активный запрос
        self.signal_history = {}  # {symbol: last_signal}
        self.previous_status = {}  # {symbol: previous_status_string}
        self.coin_data = {}  # {symbol: last_rsi}
        self.last_notification_time = {}  # {symbol: timestamp} для антиспама
        self.pending_signals = []  # Очередь сигналов, которые не были обработаны

        # Флаги для потоков
        self.running = True

        # Минимальный интервал между повторными сообщениями (секунды)
        self.min_notification_interval = 300  # 5 минут для одного символа

        logging.info(f"Бот инициализирован. Настройки загружены из {config_file}")

    def get_status_string(self, rsi, current_signal):
        """
        Получение строки статуса для сравнения

        Args:
            rsi: значение RSI
            current_signal: текущий сигнал (B, S, b, s, '')

        Returns:
            str: статусная строка
        """
        if current_signal == 'B':
            return "СИЛЬНАЯ_ПРОДАЖА"
        elif current_signal == 'S':
            return "СИЛЬНАЯ_ПОКУПКА"
        elif current_signal == 'b':
            return "НАЧАЛО_ПРОДАЖИ"
        elif current_signal == 's':
            return "НАЧАЛО_ПОКУПКИ"
        else:
            # Нейтральная зона с указанием RSI для контекста
            if rsi > 50:
                return f"НЕЙТРАЛЬНО_ВЫШЕ_{int(rsi)}"
            else:
                return f"НЕЙТРАЛЬНО_НИЖЕ_{int(rsi)}"

    def should_send_notification(self, symbol, new_status):
        """
        Проверка, нужно ли отправлять уведомление

        Args:
            symbol: символ монеты
            new_status: новый статус

        Returns:
            bool: нужно ли отправлять
        """
        # Получаем предыдущий статус
        old_status = self.previous_status.get(symbol, "")

        # Если статус не изменился - не отправляем
        if old_status == new_status:
            return False

        # Проверка на антиспам (не отправлять слишком часто для одного символа)
        current_time = time.time()
        last_time = self.last_notification_time.get(symbol, 0)

        if current_time - last_time < self.min_notification_interval:
            logging.info(f"Антиспам для {symbol}: статус изменился с {old_status} на {new_status}, "
                        f"но прошло всего {current_time - last_time:.0f} секунд")
            return False

        # Обновляем время последнего уведомления
        self.last_notification_time[symbol] = current_time

        # Статус изменился - отправляем
        return True

    def send_message(self, message, peer_id=None):
        """
        Отправка сообщения в VK

        Args:
            message: текст сообщения
            peer_id: ID получателя (опционально)
        """
        if peer_id is None:
            peer_id = self.recipient_id

        # Разбиваем длинные сообщения на части (VK ограничение 4096 символов)
        if len(message) > 4000:
            parts = [message[i:i+4000] for i in range(0, len(message), 4000)]
            for part in parts:
                self._send_single_message(part, peer_id)
        else:
            self._send_single_message(message, peer_id)

    def _send_single_message(self, message, peer_id):
        """Отправка одного сообщения"""
        try:
            self.vk.messages.send(
                peer_id=peer_id,
                message=message,
                random_id=random.randint(1, 2**31)
            )
            logging.info(f"Сообщение отправлено: {message[:50]}...")
        except Exception as e:
            logging.error(f"Ошибка при отправке сообщения: {e}")
            print(f"✗ Ошибка при отправке: {e}")

    def reload_config(self):
        """
        Перезагрузка настроек из файла
        """
        if self.config_manager.load_config():
            # Обновляем калькулятор с новыми настройками
            self.calculator.config = self.config_manager
            self.send_message("✅ Настройки успешно перезагружены из файла settings.txt\n\n" +
                            self.config_manager.get_settings_text())
            logging.info("Настройки перезагружены по команде пользователя")
        else:
            self.send_message("❌ Ошибка при перезагрузке настроек! Проверьте файл settings.txt")

    def ask_for_target_move(self, symbol, trade_type, rsi):
        """
        Запрос процента движения у пользователя
        Если уже есть ожидающий запрос, новый добавляется в очередь
        """
        risk_amount = self.calculator.calculate_risk_amount_usdt()
        rr_text = f"{self.config_manager.risk_reward_ratio}:1"

        message = f"📊 {symbol} (RSI: {rsi})\n"
        message += f"⚡ Риск на сделку: {self.config_manager.risk_percent}% ({risk_amount} USDT)\n"
        message += f"⚙️ Плечо: {self.config_manager.leverage}x | RR: {rr_text}\n"

        if trade_type == 'B':
            message += f"📈 СИГНАЛ НА ПРОДАЖУ (ШОРТ)\n"
            message += f"Введите процент движения, который хотите забрать (например, 2.5):"
        else:
            message += f"📉 СИГНАЛ НА ПОКУПКУ (ЛОНГ)\n"
            message += f"Введите процент движения, который хотите забрать (например, 2.5):"

        # Если уже есть ожидающий запрос, добавляем в очередь
        if self.waiting_for_target is not None:
            self.pending_signals.append({
                'symbol': symbol,
                'trade_type': trade_type,
                'rsi': rsi,
                'message': message,
                'timestamp': time.time()
            })
            logging.info(f"Сигнал для {symbol} добавлен в очередь (текущий активный запрос: "
                         f"{self.waiting_for_target['symbol']})")
            # Отправляем уведомление, что сигнал в очереди
            self.send_message(f"⏳ Сигнал для {symbol} поставлен в очередь. "
                            f"Дождитесь ответа на текущий запрос или отправьте 'skip' для пропуска.")
        else:
            # Устанавливаем активный запрос
            self.waiting_for_target = {
                'symbol': symbol,
                'trade_type': trade_type,
                'rsi': rsi
            }
            self.send_message(message)

    def skip_current_request(self):
        """Пропуск текущего запроса"""
        if self.waiting_for_target is not None:
            symbol = self.waiting_for_target['symbol']
            self.send_message(f"⏭️ Запрос для {symbol} пропущен. Вы можете ответить на него позже или "
                              f"он будет заменен новым сигналом.")
            logging.info(f"Пользователь пропустил запрос для {symbol}")

            # Сбрасываем текущий запрос
            self.waiting_for_target = None

            # Проверяем очередь
            self.process_next_pending_signal()
            return True
        else:
            self.send_message("❌ Нет активных запросов для пропуска")
            return False

    def process_next_pending_signal(self):
        """Обработка следующего сигнала из очереди"""
        if self.pending_signals and self.waiting_for_target is None:
            next_signal = self.pending_signals.pop(0)
            self.waiting_for_target = {
                'symbol': next_signal['symbol'],
                'trade_type': next_signal['trade_type'],
                'rsi': next_signal['rsi']
            }
            self.send_message(next_signal['message'])
            logging.info(f"Обработан следующий сигнал из очереди для {next_signal['symbol']}")

    def process_target_response(self, user_message):
        """
        Обработка ответа с процентом движения
        Args:
            user_message: текст сообщения от пользователя
        Returns:
            bool: был ли обработан ответ
        """
        # Проверяем, есть ли активный запрос
        if self.waiting_for_target is None:
            return False

        try:
            target_percent = float(user_message.strip())

            if target_percent <= 0:
                self.send_message(f"❌ Ошибка! Процент движения должен быть больше 0. Введите число > 0")
                return True

            if target_percent > 50:
                self.send_message(f"⚠️ Предупреждение: {target_percent}% - очень высокое целевое движение!")

            # Получаем данные активного запроса
            symbol = self.waiting_for_target['symbol']
            trade_type = self.waiting_for_target['trade_type']
            current_rsi = self.waiting_for_target['rsi']
            current_price = self.coin_data.get(f"{symbol}_price")

            # Получаем информацию о позиции
            position = self.calculator.get_position_info(
                target_move_percent=target_percent,
                current_price=current_price,
                trade_type=trade_type
            )

            if not position:
                self.send_message(f"❌ Ошибка расчета позиции для {symbol}")
                return True

            # Формируем красивый ответ
            if trade_type == 'B':
                response = f"📈 ПРОДАЖА (ШОРТ) {symbol}\n"
            else:
                response = f"📉 ПОКУПКА (ЛОНГ) {symbol}\n"

            response += f"┌─────────────────────────────────\n"
            response += f"│ RSI: {current_rsi}\n"
            response += f"│ Депозит: ${position['deposit']} USDT\n"
            response += f"│ Риск: {position['fixed_risk_percent']}%\n"
            response += f"│ Сумма риска: ${position['risk_amount_usdt']} USDT\n"
            response += f"│ Плечо: {position['leverage']}x\n"
            response += f"├─────────────────────────────────\n"
            response += f"│ Целевое движение: {position['target_move_percent']}%\n"
            response += f"│ TP (1:{position['risk_reward_ratio']}): {position['take_profit_percent']}%\n"
            response += f"│ Объем позиции: ${position['position_volume_usdt']} USDT\n"

            if current_price:
                response += f"├─────────────────────────────────\n"
                response += f"│ Цена входа: ${position['current_price']}\n"
                response += f"│ Кол-во: {position['position_volume_coins']} {symbol.replace('USDT', '')}\n"
                response += f"│ SL: ${position['stop_loss_price']} ({position['stop_loss_percent']}%)\n"
                response += f"│ TP: ${position['take_profit_price']} ({position['take_profit_percent']}%)\n"

            response += f"└─────────────────────────────────"

            # Добавляем пояснение
            '''response += f"\n\n💡 Пояснение:\n"
            response += f"Риск {self.config_manager.risk_percent}% от депозита (${position['risk_amount_usdt']})\n"
            response += f"при целевом движении {target_percent}% и плече {self.config_manager.leverage}x\n"
            response += f"дает объем позиции ${position['position_volume_usdt']}\n"
            response += f"TP = {target_percent}% × {self.config_manager.risk_reward_ratio} = {position['take_profit_percent']}%"'''

            self.send_message(response)

            # Сбрасываем активный запрос
            self.waiting_for_target = None

            # Проверяем очередь сигналов
            self.process_next_pending_signal()

            logging.info(f"Расчет позиции для {symbol}: цель={target_percent}%, "
                        f"объем={position['position_volume_usdt']} USDT, "
                        f"риск={position['risk_amount_usdt']} USDT")

            return True

        except ValueError:
            # Если это не число, значит пользователь отправил команду или просто текст
            # Не сбрасываем запрос, даем возможность ввести число
            if user_message.lower() not in ['skip', 'пропустить', 'отмена', 'cancel']:
                self.send_message(f"❌ Ошибка! Введите число (процент движения, например: 2.5)\n"
                                f"Текущий активный запрос: {self.waiting_for_target['symbol']}\n"
                                f"Для пропуска запроса отправьте 'skip'")
            return False

    def handle_incoming_messages(self):
        """Обработка входящих сообщений (в отдельном потоке)"""
        while self.running:
            try:
                for event in self.longpoll.listen():
                    if not self.running:
                        break

                    if event.type == VkEventType.MESSAGE_NEW and event.to_me:
                        user_message = event.text.strip()

                        # Команда пропуска текущего запроса
                        if user_message.lower() in ['skip', 'пропустить', 'отмена', 'cancel']:
                            self.skip_current_request()
                            continue

                        # Команда перезагрузки конфигурации
                        if user_message.lower() in ['reload config', 'reload', 'update config']:
                            # Сбрасываем активный запрос при перезагрузке
                            if self.waiting_for_target:
                                self.waiting_for_target = None
                                self.send_message("🔄 Активный запрос сброшен при перезагрузке настроек")
                            self.reload_config()
                            continue

                        # Команды для изменения настроек (сбрасывают активный запрос)
                        if any(user_message.lower().startswith(cmd) for cmd in ['set deposit', 'set risk',
                                                                                'set leverage', 'set rr']):
                            # Сбрасываем активный запрос
                            if self.waiting_for_target:
                                self.waiting_for_target = None
                                self.send_message("🔄 Активный запрос сброшен для изменения настроек")
                            self.process_command(user_message)
                            continue

                        # Другие команды (сбрасывают активный запрос)
                        if user_message.lower() in ['статус', 'status', 'риск', 'помощь', 'help']:
                            # Сбрасываем активный запрос
                            if self.waiting_for_target:
                                self.waiting_for_target = None
                                self.send_message("🔄 Активный запрос сброшен")
                            self.process_command(user_message)
                            continue

                        # Пытаемся обработать как ответ на запрос процента
                        if not self.process_target_response(user_message):
                            # Если не число и не команда, отправляем подсказку
                            if user_message:
                                self.send_message(f"🤔 Неизвестная команда. Для справки отправьте 'помощь'\n"
                                                f"Если вы хотели ответить на запрос - введите число (например, 2.5)\n"
                                                f"Для пропуска текущего запроса отправьте 'skip'")

            except Exception as e:
                logging.error(f"Ошибка в обработчике сообщений: {e}")
                time.sleep(1)

    def process_command(self, user_message):
        """Обработка команд"""
        if user_message.lower() in ['статус', 'status']:
            self.send_status()
        elif user_message.lower() in ['помощь', 'help']:
            self.send_help()
        elif user_message.lower() == 'риск':
            self.send_risk_info()
        elif user_message.lower().startswith('set deposit'):
            try:
                new_deposit = float(user_message.split()[2])
                if self.config_manager.update_setting('deposit', new_deposit):
                    self.send_message(f"✅ Депозит изменен на ${new_deposit} USDT\n"
                                     f"Сумма риска: ${self.calculator.calculate_risk_amount_usdt()} USDT")
                else:
                    self.send_message(f"❌ Ошибка при обновлении депозита")
            except:
                self.send_message("❌ Используйте: set deposit 10000")
        elif user_message.lower().startswith('set risk'):
            try:
                new_risk = float(user_message.split()[2])
                if 0.1 <= new_risk <= 100:
                    if self.config_manager.update_setting('risk', new_risk):
                        self.send_message(f"✅ Риск изменен на {new_risk}%\n"
                                         f"Сумма риска: ${self.calculator.calculate_risk_amount_usdt()} USDT")
                else:
                    self.send_message(f"❌ Ошибка! Риск должен быть от 0.1% до 100%")
            except:
                self.send_message("❌ Используйте: set risk 0.22")
        elif user_message.lower().startswith('set leverage'):
            try:
                new_leverage = int(user_message.split()[2])
                if 1 <= new_leverage <= 100:
                    if self.config_manager.update_setting('leverage', new_leverage):
                        self.send_message(f"✅ Плечо изменено на {new_leverage}x")
                else:
                    self.send_message(f"❌ Ошибка! Плечо должно быть от 1 до 100")
            except:
                self.send_message("❌ Используйте: set leverage 5")
        elif user_message.lower().startswith('set rr'):
            try:
                new_rr = float(user_message.split()[2])
                if 0.06 <= new_rr <= 10:
                    if self.config_manager.update_setting('rr', new_rr):
                        rr_text = f"{new_rr}:1"
                        self.send_message(f"✅ Соотношение риск/прибыль изменено на {rr_text}")
                else:
                    self.send_message(f"❌ Ошибка! RR должен быть от 0.06 до 10")
            except:
                self.send_message("❌ Используйте: set rr 2")

    def send_risk_info(self):
        """Отправка информации о текущем риске"""
        self.send_message(self.config_manager.get_settings_text())

    def send_status(self):
        """Отправка текущего статуса бота"""
        rr_text = f"{self.config_manager.risk_reward_ratio}:1"

        status = "📊 СТАТУС БОТА\n"
        status += f"├─ Депозит: ${self.config_manager.deposit} USDT\n"
        status += f"├─ Риск: {self.config_manager.risk_percent}%\n"
        status += f"├─ Сумма риска: ${self.calculator.calculate_risk_amount_usdt()} USDT\n"
        status += f"├─ Плечо: {self.config_manager.leverage}x\n"
        status += f"├─ R:R: {rr_text}\n"
        status += f"├─ Активных сигналов: {sum(1 for s in self.signal_history.values() if s)}\n"

        if self.waiting_for_target:
            status += f"├─ Активный запрос: {self.waiting_for_target['symbol']}\n"

        if self.pending_signals:
            status += f"├─ В очереди: {len(self.pending_signals)} сигнал(ов)\n"

        if self.coin_data:
            status += "└─ Мониторинг:\n"
            for symbol, rsi in self.coin_data.items():
                if not symbol.endswith('_price'):
                    signal = self.signal_history.get(symbol, '')
                    status_symbol = ""
                    if signal == 'B':
                        status_symbol = "🔴 СИЛЬНАЯ ПРОДАЖА"
                    elif signal == 'S':
                        status_symbol = "🟢 СИЛЬНАЯ ПОКУПКА"
                    elif signal == 'b':
                        status_symbol = "🟡 НАЧАЛО ПРОДАЖИ"
                    elif signal == 's':
                        status_symbol = "🟢 НАЧАЛО ПОКУПКИ"
                    else:
                        status_symbol = "⚪ НЕЙТРАЛЬНО"

                    price = self.coin_data.get(f"{symbol}_price", '?')
                    status += f"   ├─ {symbol}: {status_symbol} | RSI={rsi} | ${price}\n"

        self.send_message(status)

    def send_help(self):
        """Отправка справки по командам"""
        help_text = "🤖 ДОСТУПНЫЕ КОМАНДЫ:\n"
        help_text += "├─ статус - показать текущее состояние\n"
        help_text += "├─ риск - показать настройки и изменить их\n"
        help_text += "├─ помощь - показать это сообщение\n"
        help_text += "├─ reload config - перезагрузить настройки из файла name.txt\n"
        help_text += "├─ skip - пропустить текущий запрос процента\n"
        help_text += "├─ set deposit 10000 - изменить депозит\n"
        help_text += "├─ set risk 0.5 - изменить риск на сделку (%)\n"
        help_text += "├─ set leverage 10 - изменить кредитное плечо\n"
        help_text += "├─ set rr 2 - изменить соотношение R:R\n"
        help_text += "└─ [число] - ответ на запрос процента движения\n\n"
        help_text += self.config_manager.get_settings_text() + "\n\n"
        help_text += f"💡 Особенности:\n"
        help_text += f"   • Бот ожидает ответ только на ОДИН запрос за раз\n"
        help_text += f"   • Новые сигналы становятся в очередь\n"
        help_text += f"   • Любая команда сбрасывает текущий запрос\n"
        help_text += f"   • Отправьте 'skip' чтобы пропустить текущий запрос\n"
        help_text += f"   • TP = целевое_движение × RR\n"
        help_text += f"   • Бот отправляет сообщения только при изменении статуса"

        self.send_message(help_text)

    def send_signal_notification(self, symbol, signal_type, rsi, price, is_strong=False):
        """
        Отправка уведомления о сигнале с проверкой изменений статуса
        """
        new_status = self.get_status_string(rsi, signal_type)

        # Проверяем, нужно ли отправлять уведомление
        if not self.should_send_notification(symbol, new_status):
            logging.info(f"Статус {symbol} не изменился или антиспам: {new_status}")
            return

        # Обновляем предыдущий статус
        self.previous_status[symbol] = new_status

        # Формируем сообщение в зависимости от типа сигнала
        if signal_type == 'B':  # Сильная продажа (шорт)
            message = f"🔴🔴🔴 СИЛЬНЫЙ СИГНАЛ ПРОДАЖИ (ШОРТ) 🔴🔴🔴\n"
            message += f"└─ {symbol}\n"
            message += f"   ├─ RSI: {rsi} (перекупленность)\n"
            message += f"   └─ Цена: ${price}\n\n"

        elif signal_type == 'S':  # Сильная покупка (лонг)
            message = f"🟢🟢🟢 СИЛЬНЫЙ СИГНАЛ ПОКУПКИ (ЛОНГ) 🟢🟢🟢\n"
            message += f"└─ {symbol}\n"
            message += f"   ├─ RSI: {rsi} (перепроданность)\n"
            message += f"   └─ Цена: ${price}\n\n"

        elif signal_type == 'b':  # Слабая продажа (начало)
            message = f"🔴 СИГНАЛ: НАЧАЛО ПРОДАЖИ 🔴\n"
            message += f"└─ {symbol}\n"
            message += f"   ├─ RSI: {rsi} (готовится к перекупленности)\n"
            message += f"   └─ Цена: ${price}"

        elif signal_type == 's':  # Слабая покупка (начало)
            message = f"🟢 СИГНАЛ: НАЧАЛО ПОКУПКИ 🟢\n"
            message += f"└─ {symbol}\n"
            message += f"   ├─ RSI: {rsi} (готовится к перепроданности)\n"
            message += f"   └─ Цена: ${price}"

        else:  # Нейтральная зона
            if self.previous_status.get(symbol, "").startswith("НЕЙТРАЛЬНО"):
                return
            message = f"⚪ СТАТУС ИЗМЕНИЛСЯ: НЕЙТРАЛЬНО ⚪\n"
            message += f"└─ {symbol}\n"
            message += f"   ├─ RSI: {rsi}\n"
            message += f"   └─ Цена: ${price}"

        # Отправляем сообщение о сигнале
        self.send_message(message)

        # Если это сильный сигнал, запрашиваем процент
        if signal_type in ['B', 'S']:
            self.ask_for_target_move(symbol, signal_type, rsi)

    def analyze_and_signal(self):
        """Анализ монет и генерация сигналов (основной цикл)"""
        while self.running:
            try:
                # Читаем список монет из файла
                with open(COINS_FILE, 'r') as file:
                    lines = file.readlines()

                for line in lines:
                    if not line.strip():
                        continue

                    symbol, interval = line.strip().split('; ')

                    # Инициализация данных для новой монеты
                    if symbol not in self.signal_history:
                        self.signal_history[symbol] = ''
                        self.previous_status[symbol] = ""
                        self.last_notification_time[symbol] = 0

                    # Анализируем монету
                    analysis = self.exchange.analyze_symbol(symbol, interval)

                    if 'error' in analysis:
                        logging.warning(f"{symbol}: {analysis['error']}")
                        continue

                    # Сохраняем данные
                    self.coin_data[symbol] = analysis['rsi']
                    self.coin_data[f"{symbol}_price"] = analysis['current_price']
                    current_rsi = analysis['rsi']
                    current_price = analysis['current_price']

                    # Логика сигналов с проверкой изменений
                    old_signal = self.signal_history[symbol]
                    new_signal = old_signal

                    # Определяем новый сигнал
                    if current_rsi >= 70:
                        new_signal = 'B'
                    elif current_rsi <= 30:
                        new_signal = 'S'
                    elif current_rsi >= 65:
                        new_signal = 'b'
                    elif current_rsi <= 35:
                        new_signal = 's'
                    else:
                        new_signal = ''

                    # Если сигнал изменился
                    if new_signal != old_signal:
                        self.signal_history[symbol] = new_signal

                        # Отправляем уведомление только при изменении
                        if new_signal == 'B':
                            self.send_signal_notification(symbol, 'B', current_rsi, current_price, True)
                        elif new_signal == 'S':
                            self.send_signal_notification(symbol, 'S', current_rsi, current_price, True)
                        elif new_signal == 'b':
                            self.send_signal_notification(symbol, 'b', current_rsi, current_price, False)
                        elif new_signal == 's':
                            self.send_signal_notification(symbol, 's', current_rsi, current_price, False)
                        elif new_signal == '' and old_signal != '':
                            self.send_signal_notification(symbol, '', current_rsi, current_price, False)

                        logging.info(f"Изменение статуса {symbol}: {old_signal} -> {new_signal}, RSI={current_rsi}")

                # Логирование текущего состояния
                print(f"\n--- {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
                print(f"Сигналы: {self.signal_history}")
                print(f"Активный запрос: {self.waiting_for_target['symbol'] if self.waiting_for_target else 'Нет'}")
                print(f"В очереди: {len(self.pending_signals)}")
                print(f"RSI: {self.coin_data}")
                print(f"Настройки: Депозит={self.config_manager.deposit}, "
                      f"Риск={self.config_manager.risk_percent}%, "
                      f"Плечо={self.config_manager.leverage}x, "
                      f"RR={self.config_manager.risk_reward_ratio}:1")

                time.sleep(CHECK_INTERVAL)

            except FileNotFoundError:
                logging.error(f"Файл {COINS_FILE} не найден!")
                self.send_message(f"❌ Ошибка: файл {COINS_FILE} не найден!")
                break
            except Exception as e:
                logging.error(f"Ошибка в основном цикле: {e}")
                time.sleep(10)

    def run(self):
        """Запуск бота (основной метод)"""
        logging.info("Запуск VK Trader Bot...")

        start_message = f"🤖 Бот запущен и начал мониторинг рынка!\n\n"
        start_message += self.config_manager.get_settings_text() + "\n\n"
        start_message += f"💡 Особенности работы:\n"
        start_message += f"   • Бот ожидает ответ только на ОДИН запрос за раз\n"
        start_message += f"   • Новые сигналы становятся в очередь\n"
        start_message += f"   • Любая команда сбрасывает текущий запрос\n"
        start_message += f"   • Отправьте 'skip' чтобы пропустить текущий запрос\n\n"
        start_message += f"📝 Команды: 'помощь' - для получения списка команд"

        self.send_message(start_message)

        # Запускаем обработчик сообщений в отдельном потоке
        message_thread = threading.Thread(target=self.handle_incoming_messages, daemon=True)
        message_thread.start()

        # Запускаем основной цикл анализа
        try:
            self.analyze_and_signal()
        except KeyboardInterrupt:
            logging.info("Бот остановлен пользователем")
            self.send_message("🤖 Бот остановлен")
        finally:
            self.running = False


# Точка входа
if __name__ == "__main__":
    # Создаем и запускаем бота
    bot = VKTraderBot(
        token=VKtoken.token,
        recipient_id=RECIPIENT_ID,
        config_file=CONFIG_FILE
    )

    bot.run()
