"""
Модуль для парсинга данных с Binance и расчета технических индикаторов
Исправленная версия: добавлена поддержка Spot/Futures,
увеличен лимит свечей, опциональное исключение формирующейся свечи
"""

import requests
import logging


class ExchangeParser:
    def __init__(self, use_spot=True):
        """
        Args:
            use_spot: если True — используется Spot API (api.binance.com),
                      если False — Futures API (fapi.binance.com).
                      Spot-данные совпадают с TradingView по умолчанию.
        """
        self.use_spot = use_spot
        self.base_url = "https://api.binance.com" if use_spot else "https://fapi.binance.com"
        logging.basicConfig(filename='py_log.log', format="%(asctime)s %(levelname)s %(message)s")
        logging.info(f"ExchangeParser инициализирован: {'Spot' if use_spot else 'Futures'} API")

    def fetch_klines(self, symbol, interval, limit=200, exclude_current_candle=True):
        """
        Получение свечей с Binance

        Args:
            symbol: торговая пара (например, 'BTCUSDT')
            interval: интервал ('1h', '4h', '1d' и т.д.)
            limit: количество свечей (рекомендуется 200+ для стабильного RSI)
            exclude_current_candle: если True, последняя (формирующаяся) свеча
                                    исключается для стабильности RSI

        Returns:
            list: список цен закрытия
        """
        endpoint = "/fapi/v1/klines" if not self.use_spot else "/api/v3/klines"
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        }

        try:
            response = requests.get(self.base_url + endpoint, params=params)
            response.raise_for_status()
            data = response.json()

            if not data:
                logging.warning(f'fetch_klines: пустой ответ для {symbol}')
                return None

            closes = [float(candle[4]) for candle in data]

            # Исключаем текущую (незакрытую) свечу для стабильности
            if exclude_current_candle and len(closes) > 1:
                closes = closes[:-1]
                logging.debug(f'{symbol}: исключена формирующаяся свеча, '
                              f'осталось {len(closes)} цен закрытия')

            return closes

        except Exception as e:
            logging.error(f'fetch_klines error for {symbol}: {e}')
            return None

    def fetch_current_price(self, symbol):
        """
        Получение текущей цены монеты

        Args:
            symbol: торговая пара

        Returns:
            float: текущая цена
        """
        endpoint = "/fapi/v1/ticker/price" if not self.use_spot else "/api/v3/ticker/price"
        params = {"symbol": symbol}

        try:
            response = requests.get(self.base_url + endpoint, params=params)
            response.raise_for_status()
            data = response.json()
            return float(data['price'])
        except Exception as e:
            logging.error(f'fetch_current_price error for {symbol}: {e}')
            return None

    def calculate_rsi(self, prices, period=14):
        """
        Расчет RSI индикатора по методу Уайлдера (Wilder's Smoothing / RMA)
        Это стандартный метод, используемый TradingView.

        Args:
            prices: список цен закрытия
            period: период RSI (по умолчанию 14)

        Returns:
            float: значение RSI
        """
        if len(prices) < period + 1:
            logging.warning(f"Need at least {period + 1} prices to compute RSI, got {len(prices)}")
            raise ValueError(f"Need at least {period + 1} prices to compute RSI, got {len(prices)}")

        # Вычисляем изменения цен
        deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]

        # Разделяем на приросты и потери
        gains = []
        losses = []
        for delta in deltas:
            if delta > 0:
                gains.append(delta)
                losses.append(0.0)
            else:
                gains.append(0.0)
                losses.append(abs(delta))

        # Начальное простое среднее (SMA) для первого периода
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        # Последующее сглаживание по Уайлдеру (RMA)
        # Это то же самое, что использует TradingView по умолчанию
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        return rsi

    def analyze_symbol(self, symbol, interval):
        """
        Полный анализ монеты

        Args:
            symbol: торговая пара
            interval: интервал свечей

        Returns:
            dict: { 'rsi': float, 'current_price': float, 'error': None или str }
        """
        try:
            # Получаем текущую цену
            current_price = self.fetch_current_price(symbol)

            # Получаем свечи для RSI
            # 200 свечей (исключая текущую) = 199 закрытых свечей — достаточно для стабильного RSI(14)
            closes = self.fetch_klines(symbol, interval, limit=200, exclude_current_candle=True)
            if closes is None:
                return {'error': 'Не удалось получить данные свечей'}

            # Рассчитываем RSI
            if len(closes) < 15:
                return {'error': f'Недостаточно данных. Получено {len(closes)} свечей'}

            rsi = round(self.calculate_rsi(closes), 2)

            return {
                'rsi': rsi,
                'current_price': current_price,
                'symbol': symbol,
                'interval': interval
            }

        except Exception as e:
            logging.error(f'analyze_symbol error for {symbol}: {e}')
            return {'error': str(e)}