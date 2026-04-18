"""
Модуль для расчета объема позиции на основе фиксированного риска
"""


class PositionCalculator:
    def __init__(self, config_manager):
        """
        Инициализация калькулятора позиций

        Args:
            config_manager: объект ConfigManager с настройками
        """
        self.config = config_manager

    def calculate_risk_amount_usdt(self):
        """
        Расчет суммы риска в USDT на основе фиксированного процента

        Returns:
            float: сумма риска в USDT
        """
        risk_amount = self.config.deposit * (self.config.risk_percent / 100)
        return round(risk_amount, 2)

    def calculate_position_size(self, target_move_percent):
        """
        Расчет объема позиции на основе целевого движения

        Args:
            target_move_percent: процент движения, который планирует забрать пользователь

        Returns:
            dict: информация о позиции
        """
        try:
            target_move_percent = float(target_move_percent)

            if target_move_percent <= 0:
                raise ValueError("Процент движения должен быть больше 0")

            # Сумма риска фиксирована (% от депозита)
            risk_amount_usdt = self.calculate_risk_amount_usdt()

            # Объем позиции с учетом кредитного плеча
            position_volume = risk_amount_usdt / (target_move_percent / 100) / self.config.leverage

            return {
                'deposit': self.config.deposit,
                'fixed_risk_percent': self.config.risk_percent,
                'risk_amount_usdt': risk_amount_usdt,
                'target_move_percent': target_move_percent,
                'leverage': self.config.leverage,
                'position_volume_usdt': round(position_volume, 2),
                'risk_reward_ratio': self.config.risk_reward_ratio
            }

        except Exception as e:
            print(f"Ошибка расчета позиции: {e}")
            return None

    def calculate_position_with_price(self, target_move_percent, current_price):
        """
        Расчет объема позиции в монетах с учетом текущей цены

        Args:
            target_move_percent: процент движения для забора
            current_price: текущая цена монеты

        Returns:
            dict: полная информация о позиции
        """
        position = self.calculate_position_size(target_move_percent)

        if position and current_price:
            position['current_price'] = current_price
            position['position_volume_coins'] = round(position['position_volume_usdt'] / current_price, 4)

            # Стоп-лосс = фиксированный риск
            position['stop_loss_percent'] = self.config.risk_percent

            # Тейк-профит = целевое движение * соотношение риск/прибыль
            position['take_profit_percent'] = round(target_move_percent * self.config.risk_reward_ratio, 2)

            # Расчет уровней стоп-лосс и тейк-профит
            if current_price:
                if position.get('trade_type') == 'S':  # Покупка (лонг)
                    position['stop_loss_price'] = round(current_price * (1 - self.config.risk_percent / 100), 2)
                    position['take_profit_price'] = round(current_price * (1 + position['take_profit_percent'] / 100), 2)
                else:  # Продажа (шорт)
                    position['stop_loss_price'] = round(current_price * (1 + self.config.risk_percent / 100), 2)
                    position['take_profit_price'] = round(current_price * (1 - position['take_profit_percent'] / 100), 2)

        return position

    def get_position_info(self, target_move_percent, current_price=None, trade_type=None):
        """
        Получение полной информации о позиции

        Args:
            target_move_percent: процент движения для забора
            current_price: текущая цена (опционально)
            trade_type: тип сделки ('B' - продажа/шорт, 'S' - покупка/лонг)

        Returns:
            dict: информация о позиции
        """
        if current_price:
            position = self.calculate_position_with_price(target_move_percent, current_price)
        else:
            position = self.calculate_position_size(target_move_percent)

        if position and trade_type:
            position['trade_type'] = trade_type
            # Пересчитываем уровни с учетом типа сделки
            if current_price:
                if trade_type == 'S':  # Покупка (лонг)
                    position['stop_loss_price'] = round(current_price * (1 - self.config.risk_percent / 100), 2)
                    position['take_profit_price'] = round(current_price * (1 + position['take_profit_percent'] / 100), 2)
                else:  # Продажа (шорт)
                    position['stop_loss_price'] = round(current_price * (1 + self.config.risk_percent / 100), 2)
                    position['take_profit_price'] = round(current_price * (1 - position['take_profit_percent'] / 100), 2)

        return position
