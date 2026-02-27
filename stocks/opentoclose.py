"""
Open to Close Analysis

Tests correlation between early morning price action and whether the close
finishes above or below the open.

Variables tested:
1. Strength of first n bars in relation to ADR
2. Distance from VWAP (in ADRs) for first n bars
3. Prior n days direction (positive/negative)
"""

import asyncio
from datetime import datetime
import numpy as np
import pandas as pd
from scipy import stats
from scipy.stats import chi2_contingency
from ib_async import IB, Stock

# Set event loop for ib_async compatibility
asyncio.set_event_loop(asyncio.new_event_loop())


class ADRStrengthCondition:
    """
    Calculates the directional movement of the first n bars as % of ADR.
    
    Measures how far price moved in the direction of the first bar.
    Positive = moved up, Negative = moved down, magnitude = % of ADR
    """

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.name = 'ADR Strength (Directional)'

    def calculate(
        self,
        ib: IB,
        symbol: str,
        first_n_bars: list,
        day_open: float,
        adr: float,
    ) -> dict | None:
        """
        Calculate directional movement of first n bars relative to ADR.
        
        Args:
            ib: IB connection instance
            symbol: Stock symbol
            first_n_bars: List of bar objects from first n minutes
            day_open: Opening price of the day
            adr: Average Daily Range value
            
        Returns:
            Dictionary with:
            - 'direction': 1 if up, -1 if down, 0 if flat
            - 'size_pct_adr': absolute movement as % of ADR
            - 'directional_size': signed movement as % of ADR (positive=up, negative=down)
        """
        if not self.enabled:
            return None

        if not first_n_bars or day_open is None or adr is None or adr <= 0:
            return None

        try:
            # Get first bar's open (should be day's open) and last bar's close
            first_bar = first_n_bars[0]
            last_bar = first_n_bars[-1]
            
            first_open = first_bar.open if first_bar.open is not None else day_open
            last_close = last_bar.close if last_bar.close is not None else None
            
            if last_close is None:
                return None

            # Calculate directional movement
            movement = last_close - first_open
            
            # Determine direction: 1 if up, -1 if down, 0 if flat
            if movement > 0.001:  # Small threshold to avoid noise
                direction = 1
            elif movement < -0.001:
                direction = -1
            else:
                direction = 0

            # Calculate size as % of ADR
            size_pct_adr = abs(movement) / adr
            directional_size = movement / adr  # Signed: positive = up, negative = down

            return {
                'direction': direction,
                'size_pct_adr': float(size_pct_adr),
                'directional_size': float(directional_size),
            }
        except Exception as e:
            print(f'Error calculating ADR strength for {symbol}: {e}')
            return None


class VWAPDistanceCondition:
    """
    Calculates directional distance from VWAP in ADRs.
    
    Positive = above VWAP, Negative = below VWAP
    Magnitude = distance as % of ADR
    """

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.name = 'VWAP Distance'

    def calculate_vwap(self, bars: list, day_open: float) -> float | None:
        """
        Calculate VWAP from day's open using only the first n bars (first 15 minutes).
        
        This ensures VWAP is calculated at the same point in time as the first bar analysis,
        avoiding forward-looking bias.
        
        Args:
            bars: List of bar objects from first n minutes (not entire day)
            day_open: Opening price of the day
            
        Returns:
            VWAP value, or None if calculation fails
        """
        if not bars or day_open is None:
            return None

        try:
            # VWAP = sum(price * volume) / sum(volume)
            # Start with day's open price and volume
            cumulative_price_volume = day_open * 1.0  # Start with open price, volume = 1
            cumulative_volume = 1.0

            for bar in bars:
                # Use typical price (HLC/3) for the bar
                typical_price = (bar.high + bar.low + bar.close) / 3.0
                volume = getattr(bar, 'volume', 1.0) or 1.0

                cumulative_price_volume += typical_price * volume
                cumulative_volume += volume

            if cumulative_volume == 0:
                return None

            vwap = cumulative_price_volume / cumulative_volume
            return float(vwap)
        except Exception as e:
            print(f'Error calculating VWAP: {e}')
            return None

    def calculate(
        self,
        ib: IB,
        symbol: str,
        first_n_bars: list,
        day_open: float,
        adr: float,
    ) -> dict | None:
        """
        Calculate directional distance from VWAP in ADRs.
        
        Args:
            ib: IB connection instance
            symbol: Stock symbol
            first_n_bars: List of bar objects from first n minutes (used for VWAP calculation)
            day_open: Opening price of the day
            adr: Average Daily Range value
            
        Returns:
            Dictionary with:
            - 'directional_distance': signed distance in ADRs (positive=above VWAP, negative=below)
            - 'absolute_distance': absolute distance in ADRs
            - 'direction': 1 if above VWAP, -1 if below, 0 if at VWAP
        """
        if not self.enabled:
            return None

        if not first_n_bars or adr is None or adr <= 0:
            return None

        try:
            # Calculate VWAP using only the first n bars (not entire day)
            vwap = self.calculate_vwap(first_n_bars, day_open)
            if vwap is None:
                return None

            # Calculate average price of first n bars
            closes = [bar.close for bar in first_n_bars if bar.close is not None]
            if not closes:
                return None

            avg_price = sum(closes) / len(closes)

            # Calculate directional distance (signed)
            directional_distance = (avg_price - vwap) / adr
            absolute_distance = abs(directional_distance)
            
            # Determine direction relative to VWAP
            if avg_price > vwap * 1.001:  # Small threshold
                direction = 1  # Above VWAP
            elif avg_price < vwap * 0.999:
                direction = -1  # Below VWAP
            else:
                direction = 0  # At VWAP

            return {
                'directional_distance': float(directional_distance),
                'absolute_distance': float(absolute_distance),
                'direction': direction,
            }
        except Exception as e:
            print(f'Error calculating VWAP distance for {symbol}: {e}')
            return None


class PriorDaysDirectionCondition:
    """
    Analyzes if prior n days were positive or negative.
    
    Returns ratio of positive days (close > open) in the prior n days.
    """

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.name = 'Prior Days Direction'

    def calculate(
        self,
        ib: IB,
        symbol: str,
        prior_days: int = 5,
    ) -> float | None:
        """
        Calculate ratio of positive days in prior n days.
        
        Args:
            ib: IB connection instance
            symbol: Stock symbol
            prior_days: Number of prior days to analyze (default: 5)
            
        Returns:
            Ratio of positive days (0.0 to 1.0), or None if calculation fails
        """
        if not self.enabled:
            return None

        try:
            contract = Stock(symbol, 'SMART', 'USD')
            ib.qualifyContracts(contract)

            # Request historical daily bars
            bars = ib.reqHistoricalData(
                contract,
                endDateTime='',
                durationStr=f'{prior_days + 1} D',  # +1 to ensure we get enough days
                barSizeSetting='1 day',
                whatToShow='TRADES',
                useRTH=True,
                formatDate=1,
            )

            if not bars or len(bars) < prior_days:
                return None

            # Take the last prior_days bars (excluding today)
            prior_bars = bars[-prior_days - 1:-1] if len(bars) > prior_days else bars[:-1]

            if not prior_bars:
                return None

            # Count positive days (close > open)
            positive_days = 0
            for bar in prior_bars:
                if bar.open is not None and bar.close is not None:
                    if bar.close > bar.open:
                        positive_days += 1

            ratio = positive_days / len(prior_bars) if prior_bars else 0.0

            return float(ratio)
        except Exception as e:
            print(f'Error calculating prior days direction for {symbol}: {e}')
            return None


class OpenToCloseAnalyzer:
    """
    Main analyzer class that coordinates all conditions and performs correlation analysis.
    """

    def __init__(
        self,
        ib: IB,
        adr_strength_enabled: bool = True,
        vwap_distance_enabled: bool = True,
        prior_days_enabled: bool = True,
        n_minutes: int = 15,
        bar_size: str = '15 mins',
        prior_days: int = 3,
        adr_days: int = 20,
    ):
        """
        Initialize the analyzer.
        
        Args:
            ib: IB connection instance
            adr_strength_enabled: Enable ADR strength condition
            vwap_distance_enabled: Enable VWAP distance condition
            prior_days_enabled: Enable prior days direction condition
            n_minutes: Number of minutes to analyze at start of day (default: 15)
            bar_size: Bar size for intraday data (default: '15 mins')
            prior_days: Number of prior days to analyze for direction (default: 3)
            adr_days: Number of days to use for ADR calculation
        """
        self.ib = ib
        self.n_minutes = n_minutes
        
        # Enforce 15 min bars for consistency
        if bar_size != '15 mins':
            print(f'Warning: Forcing bar_size to "15 mins" (was "{bar_size}")')
        self.bar_size = '15 mins'
        
        self.prior_days = prior_days
        self.adr_days = adr_days

        # Initialize condition classes
        self.adr_strength = ADRStrengthCondition(enabled=adr_strength_enabled)
        self.vwap_distance = VWAPDistanceCondition(enabled=vwap_distance_enabled)
        self.prior_days_dir = PriorDaysDirectionCondition(enabled=prior_days_enabled)
        
        # Cache for ADR (same for all days of same symbol) - speeds up analysis
        self._adr_cache = {}

    def get_daily_bars(self, symbol: str, days: int = 1) -> list | None:
        """
        Get daily bars for a symbol.
        
        Args:
            symbol: Stock symbol
            days: Number of days to request (if > 365, will use years)
            
        Returns:
            List of daily bars, or None if fails
        """
        try:
            contract = Stock(symbol, 'SMART', 'USD')
            self.ib.qualifyContracts(contract)

            # IB requires durations > 365 days to be specified in years
            if days > 365:
                years = max(1, int(days / 365.0) + 1)  # Round up to ensure we get enough data
                duration_str = f'{years} Y'
            else:
                duration_str = f'{days} D'

            bars = self.ib.reqHistoricalData(
                contract,
                endDateTime='',
                durationStr=duration_str,
                barSizeSetting='1 day',
                whatToShow='TRADES',
                useRTH=True,
                formatDate=1,
            )

            return bars if bars else None
        except Exception as e:
            print(f'Error getting daily bars for {symbol}: {e}')
            return None

    def get_intraday_bars(self, symbol: str, date: datetime | None = None) -> list | None:
        """
        Get intraday bars for a specific date - only requests what we need (first n minutes).
        
        Args:
            symbol: Stock symbol
            date: Date to get bars for (default: today)
            
        Returns:
            List of intraday bars, or None if fails
        """
        try:
            contract = Stock(symbol, 'SMART', 'USD')
            self.ib.qualifyContracts(contract)

            # Only request the bars we need: first n minutes + small buffer
            # This is much faster than requesting the full day
            duration_minutes = self.n_minutes + 15  # Add small buffer
            duration_seconds = duration_minutes * 60

            # Use specific date if provided, otherwise use today
            # For historical dates, set time to market open (9:30 AM ET) + n_minutes
            if date:
                # Handle different date types from IB bars
                if isinstance(date, datetime):
                    # Set to 9:30 AM ET + n_minutes for end of first bar period
                    end_date = date.replace(hour=9, minute=30 + self.n_minutes, second=0, microsecond=0)
                elif hasattr(date, 'date'):  # Might be a datetime with .date() method
                    dt = date.date() if callable(date.date) else date
                    if isinstance(dt, datetime):
                        end_date = dt.replace(hour=9, minute=30 + self.n_minutes, second=0, microsecond=0)
                    else:
                        # It's a date object, convert to datetime at market open + n_minutes
                        end_date = datetime.combine(dt, datetime.min.time()).replace(hour=9, minute=30 + self.n_minutes)
                else:
                    # Try to convert to datetime
                    end_date = datetime.combine(date, datetime.min.time()).replace(hour=9, minute=30 + self.n_minutes)
            else:
                # For today, use current time or market open + n_minutes
                now = datetime.now()
                if now.hour < 9 or (now.hour == 9 and now.minute < 30):
                    # Before market open, use market open + n_minutes
                    end_date = now.replace(hour=9, minute=30 + self.n_minutes, second=0, microsecond=0)
                else:
                    end_date = now

            bars = self.ib.reqHistoricalData(
                contract,
                endDateTime=end_date,
                durationStr=f'{duration_seconds} S',
                barSizeSetting=self.bar_size,
                whatToShow='TRADES',
                useRTH=True,
                formatDate=1,
            )

            return bars if bars else None
        except Exception as e:
            print(f'Error getting intraday bars for {symbol}: {e}')
            return None

    def calculate_adr(self, symbol: str) -> float | None:
        """
        Calculate ADR for a symbol (cached for performance).
        
        ADR is the same for all days of the same symbol, so we cache it.
        """
        # Check cache first
        if symbol in self._adr_cache:
            return self._adr_cache[symbol]
        
        try:
            contract = Stock(symbol, 'SMART', 'USD')
            self.ib.qualifyContracts(contract)

            bars = self.ib.reqHistoricalData(
                contract,
                endDateTime='',
                durationStr=f'{self.adr_days} D',
                barSizeSetting='1 day',
                whatToShow='TRADES',
                useRTH=True,
                formatDate=1,
            )

            if not bars:
                return None

            daily_ranges = [
                bar.high - bar.low
                for bar in bars
                if bar.high is not None and bar.low is not None
            ]

            if not daily_ranges:
                return None

            adr = sum(daily_ranges) / len(daily_ranges)
            adr_value = round(float(adr), 2)

            # Cache the result
            self._adr_cache[symbol] = adr_value
            return adr_value
        except Exception as e:
            print(f'Error calculating ADR for {symbol}: {e}')
            return None

    def calculate_5day_ma(self, symbol: str, date: datetime | None = None) -> float | None:
        """
        Calculate 5-day moving average closing price.
        
        Args:
            symbol: Stock symbol
            date: Date to calculate MA for (uses prior 5 days ending on this date)
            
        Returns:
            5-day MA value, or None if calculation fails
        """
        try:
            contract = Stock(symbol, 'SMART', 'USD')
            self.ib.qualifyContracts(contract)

            # Request 6 days to ensure we get 5 days of data (accounting for weekends/holidays)
            bars = self.ib.reqHistoricalData(
                contract,
                endDateTime=date if date else '',
                durationStr='6 D',
                barSizeSetting='1 day',
                whatToShow='TRADES',
                useRTH=True,
                formatDate=1,
            )

            if not bars or len(bars) < 5:
                return None

            # Get closes from last 5 bars
            closes = [
                bar.close
                for bar in bars[-5:]
                if bar.close is not None
            ]

            if len(closes) < 5:
                return None

            ma_5day = sum(closes) / len(closes)
            return float(ma_5day)
        except Exception as e:
            print(f'Error calculating 5-day MA for {symbol}: {e}')
            return None

    def analyze_day(
        self,
        symbol: str,
        date: datetime | None = None,
        daily_bar: object | None = None,
    ) -> dict | None:
        """
        Analyze a single day for a symbol.
        
        Args:
            symbol: Stock symbol
            date: Date to analyze (optional, used for intraday bars)
            daily_bar: Daily bar object with open/close (optional, if not provided will fetch)
        
        Returns:
            Dictionary with condition values and outcome (close > open), or None if fails
        """
        try:
            # Get daily bar for open/close
            if daily_bar is None:
                if date:
                    # For historical dates, request enough days to find the target
                    daily_bars = self.get_daily_bars(symbol, days=10)
                    if not daily_bars:
                        return None
                    
                    # Find the bar for the target date
                    target_date_only = date.date() if isinstance(date, datetime) else date
                    today_bar = None
                    for bar in daily_bars:
                        bar_date = bar.date.date() if hasattr(bar.date, 'date') else bar.date
                        if bar_date == target_date_only:
                            today_bar = bar
                            break
                    
                    if today_bar is None:
                        return None
                else:
                    # For current date, get recent bars
                    daily_bars = self.get_daily_bars(symbol, days=2)
                    if not daily_bars or len(daily_bars) < 1:
                        return None
                    today_bar = daily_bars[-1]
            else:
                today_bar = daily_bar

            day_open = today_bar.open
            day_close = today_bar.close

            if day_open is None or day_close is None:
                return None

            # Determine outcome: 1 if close > open, 0 otherwise
            outcome = 1 if day_close > day_open else 0

            # Get intraday bars - ensure we're using 15 min bars
            if self.bar_size != '15 mins':
                print(f'Warning: Expected 15 mins bars, got {self.bar_size}')
            
            intraday_bars = self.get_intraday_bars(symbol, date)
            if not intraday_bars:
                return None

            # Filter bars to first n minutes
            # For 15 min bars, 15 minutes = 1 bar
            bars_needed = max(1, self.n_minutes // 15)
            first_n_bars = intraday_bars[:bars_needed] if len(intraday_bars) >= bars_needed else intraday_bars

            if not first_n_bars:
                return None

            # Calculate ADR
            adr = self.calculate_adr(symbol)
            if adr is None:
                return None

            # Calculate 5-day moving average
            ma_5day = self.calculate_5day_ma(symbol, date)
            open_above_ma = None
            if ma_5day is not None:
                open_above_ma = 1 if day_open > ma_5day else 0

            # Calculate condition values
            result = {
                'symbol': symbol,
                'date': date or datetime.now(),
                'outcome': outcome,  # 1 = close > open, 0 = close <= open
                'open': day_open,
                'close': day_close,
                'ma_5day': ma_5day,
                'open_above_ma': open_above_ma,  # 1 if open > 5-day MA, 0 if below
            }

            # ADR Strength (directional)
            adr_strength_data = self.adr_strength.calculate(
                self.ib,
                symbol,
                first_n_bars,
                day_open,
                adr,
            )
            if adr_strength_data:
                result['first_bar_direction'] = adr_strength_data['direction']
                result['first_bar_size_pct_adr'] = adr_strength_data['size_pct_adr']
                result['first_bar_directional_size'] = adr_strength_data['directional_size']
            else:
                result['first_bar_direction'] = None
                result['first_bar_size_pct_adr'] = None
                result['first_bar_directional_size'] = None

            # VWAP Distance (directional)
            # VWAP is now calculated using only first_n_bars (not entire day)
            vwap_distance_data = self.vwap_distance.calculate(
                self.ib,
                symbol,
                first_n_bars,
                day_open,
                adr,
            )
            if vwap_distance_data:
                result['vwap_directional_distance'] = vwap_distance_data['directional_distance']
                result['vwap_absolute_distance'] = vwap_distance_data['absolute_distance']
                result['vwap_direction'] = vwap_distance_data['direction']
            else:
                result['vwap_directional_distance'] = None
                result['vwap_absolute_distance'] = None
                result['vwap_direction'] = None

            # Prior Days Direction
            prior_days_ratio = self.prior_days_dir.calculate(
                self.ib,
                symbol,
                self.prior_days,
            )
            result['prior_days_ratio'] = prior_days_ratio

            return result
        except Exception as e:
            print(f'Error analyzing day for {symbol}: {e}')
            return None

    def analyze_multiple_days(
        self,
        symbol: str,
        num_days: int = 252,
    ) -> list[dict]:
        """
        Analyze multiple days for a symbol.
        
        Args:
            symbol: Stock symbol
            num_days: Number of days to analyze (default: 252 trading days = ~1 year)
            
        Returns:
            List of analysis results
        """
        results = []

        # Get historical daily bars to find dates
        # Request extra days to account for weekends/holidays
        daily_bars = self.get_daily_bars(symbol, days=int(num_days * 1.5))
        if not daily_bars:
            return results

        # Pre-calculate ADR once (same for all days) - speeds up analysis
        print(f'  Calculating ADR for {symbol}...')
        adr = self.calculate_adr(symbol)
        if adr is None:
            print(f'  Warning: Could not calculate ADR for {symbol}')
        else:
            print(f'  ADR: ${adr:.2f} (cached for all days)')

        # Analyze each day, going backwards from most recent
        analyzed_count = 0
        skipped_count = 0
        for i in range(1, len(daily_bars)):
            if analyzed_count >= num_days:
                break
                
            # Use the date from the bar
            bar = daily_bars[-(i + 1)]
            bar_date = bar.date if hasattr(bar, 'date') else None

            # Pass the daily_bar directly to avoid re-fetching
            result = self.analyze_day(symbol, date=bar_date, daily_bar=bar)
            if result:
                results.append(result)
                analyzed_count += 1
                if analyzed_count % 50 == 0:
                    print(f'  Analyzed {analyzed_count} days for {symbol}...')
            else:
                skipped_count += 1
                if skipped_count <= 5:  # Only print first few failures
                    print(f'  Skipped day {bar_date} (missing data)')
        
        if skipped_count > 5:
            print(f'  ... and {skipped_count - 5} more days skipped')

        return results

    def calculate_correlations(self, results: list[dict]) -> dict:
        """
        Calculate correlation statistics testing directional hypothesis.
        
        Tests:
        1. Does first bar direction predict day direction? (isolated)
        2. Does first bar size (% ADR) improve prediction? (isolated)
        2b. Does open > 5-day MA improve first bar direction prediction? (conditional/combined)
        3. Does VWAP distance magnitude help predict direction? (isolated)
        4. Does VWAP distance help when first bar direction is known? (combined)
        5. Does prior days ratio help when first bar direction matches? (combined)
        6. Does first bar size + VWAP distance together improve prediction? (combined)
        7. All variables together - multivariate analysis (cumulative)
        
        Args:
            results: List of analysis results from analyze_multiple_days
            
        Returns:
            Dictionary with correlation statistics for each test
        """
        if not results:
            return {}

        df = pd.DataFrame(results)
        correlations = {}

        # Test 1: First bar direction predicts day direction (isolated)
        # Outcome: 1 if day closes up, 0 if closes down
        # First bar direction: 1 if up, -1 if down, 0 if flat
        if self.adr_strength.enabled and 'first_bar_direction' in df.columns:
            dir_df = df[['first_bar_direction', 'outcome']].dropna()
            # Filter out flat bars (direction = 0) for cleaner analysis
            dir_df = dir_df[dir_df['first_bar_direction'] != 0]
            
            if len(dir_df) > 1:
                # Convert direction to binary: 1 if up, 0 if down (to match outcome)
                dir_df = dir_df.copy()
                dir_df['first_bar_up'] = (dir_df['first_bar_direction'] > 0).astype(int)
                
                dir_values = dir_df['first_bar_up'].to_numpy()
                outcomes = dir_df['outcome'].to_numpy()
                
                if len(set(dir_values)) > 1 and len(set(outcomes)) > 1:
                    pearson_result = stats.pearsonr(dir_values, outcomes)
                    # Calculate accuracy: % of times direction matches
                    matches = (dir_values == outcomes).sum()
                    accuracy = matches / len(dir_values) if len(dir_values) > 0 else 0
                    
                    correlations['first_bar_direction_prediction'] = {
                        'pearson_r': float(pearson_result[0]),
                        'pearson_p': float(pearson_result[1]),
                        'n_samples': len(dir_values),
                        'significant': pearson_result[1] < 0.05,
                        'accuracy': float(accuracy),
                        'description': 'First bar direction (up/down) predicts day direction',
                    }

        # Test 2: First bar size (% ADR) correlates with day finishing in same direction
        # Only test when first bar direction matches day direction
        if self.adr_strength.enabled and 'first_bar_size_pct_adr' in df.columns:
            size_df = df[['first_bar_direction', 'first_bar_size_pct_adr', 'outcome']].dropna()
            size_df = size_df[size_df['first_bar_direction'] != 0]
            
            if len(size_df) > 1:
                # Only look at cases where first bar direction matches day direction
                size_df = size_df.copy()
                size_df['first_bar_up'] = (size_df['first_bar_direction'] > 0).astype(int)
                size_df['direction_match'] = (size_df['first_bar_up'] == size_df['outcome']).astype(int)
                
                # Test: Does larger size improve accuracy?
                matched_df = size_df[size_df['direction_match'] == 1]
                if len(matched_df) > 1:
                    size_values = matched_df['first_bar_size_pct_adr'].to_numpy()
                    # For matched cases, see if larger size correlates with stronger outcome
                    outcomes = matched_df['outcome'].to_numpy()
                    
                    if len(set(size_values)) > 1:
                        pearson_result = stats.pearsonr(size_values, outcomes)
                        correlations['first_bar_size_effect'] = {
                            'pearson_r': float(pearson_result[0]),
                            'pearson_p': float(pearson_result[1]),
                            'n_samples': len(size_values),
                            'significant': pearson_result[1] < 0.05,
                            'description': 'First bar size (% ADR) effect when direction matches',
                        }

        # Test 2b: Does open > 5-day MA improve first bar direction prediction?
        # Hypothesis: When open is above 5-day MA, first bar direction has better predictive power
        if (self.adr_strength.enabled and 'first_bar_direction' in df.columns and
            'open_above_ma' in df.columns):
            ma_df = df[['first_bar_direction', 'open_above_ma', 'outcome']].dropna()
            ma_df = ma_df[ma_df['first_bar_direction'] != 0]  # Filter out flat bars
            
            if len(ma_df) > 1:
                ma_df = ma_df.copy()
                ma_df['first_bar_up'] = (ma_df['first_bar_direction'] > 0).astype(int)
                
                # Split into above MA and below MA groups
                above_ma_df = ma_df[ma_df['open_above_ma'] == 1]
                below_ma_df = ma_df[ma_df['open_above_ma'] == 0]
                
                above_accuracy = None
                below_accuracy = None
                above_n = 0
                below_n = 0
                
                # Calculate accuracy for above MA
                if len(above_ma_df) > 0:
                    above_dir = above_ma_df['first_bar_up'].to_numpy()
                    above_outcomes = above_ma_df['outcome'].to_numpy()
                    above_matches = (above_dir == above_outcomes).sum()
                    above_accuracy = above_matches / len(above_dir) if len(above_dir) > 0 else 0
                    above_n = len(above_dir)
                
                # Calculate accuracy for below MA
                if len(below_ma_df) > 0:
                    below_dir = below_ma_df['first_bar_up'].to_numpy()
                    below_outcomes = below_ma_df['outcome'].to_numpy()
                    below_matches = (below_dir == below_outcomes).sum()
                    below_accuracy = below_matches / len(below_dir) if len(below_dir) > 0 else 0
                    below_n = len(below_dir)
                
                # Test if difference is significant using chi-square test
                if above_accuracy is not None and below_accuracy is not None:
                    # Create contingency table
                    above_correct = int(above_accuracy * above_n) if above_n > 0 else 0
                    above_incorrect = above_n - above_correct
                    below_correct = int(below_accuracy * below_n) if below_n > 0 else 0
                    below_incorrect = below_n - below_correct
                    
                    contingency = [[above_correct, above_incorrect],
                                   [below_correct, below_incorrect]]
                    
                    try:
                        chi2, p_value, dof, expected = chi2_contingency(contingency)
                        correlations['open_above_ma_effect'] = {
                            'above_ma_accuracy': float(above_accuracy),
                            'below_ma_accuracy': float(below_accuracy),
                            'accuracy_difference': float(above_accuracy - below_accuracy),
                            'above_ma_n': int(above_n),
                            'below_ma_n': int(below_n),
                            'p_value': float(p_value),
                            'significant': p_value < 0.05,
                            'description': 'First bar direction prediction accuracy: Open > 5-day MA vs Open < 5-day MA',
                        }
                    except Exception:
                        # If chi-square fails, just report the accuracies
                        correlations['open_above_ma_effect'] = {
                            'above_ma_accuracy': float(above_accuracy),
                            'below_ma_accuracy': float(below_accuracy),
                            'accuracy_difference': float(above_accuracy - below_accuracy),
                            'above_ma_n': int(above_n),
                            'below_ma_n': int(below_n),
                            'p_value': None,
                            'significant': False,
                            'description': 'First bar direction prediction accuracy: Open > 5-day MA vs Open < 5-day MA',
                        }

        # Test 3: VWAP absolute distance helps predict direction (isolated)
        if self.vwap_distance.enabled and 'vwap_absolute_distance' in df.columns:
            vwap_df = df[['vwap_absolute_distance', 'outcome']].dropna()
            if len(vwap_df) > 1:
                vwap_values = vwap_df['vwap_absolute_distance'].to_numpy()
                outcomes = vwap_df['outcome'].to_numpy()
                
                if len(set(vwap_values)) > 1 and len(set(outcomes)) > 1:
                    pearson_result = stats.pearsonr(vwap_values, outcomes)
                    correlations['vwap_distance_prediction'] = {
                        'pearson_r': float(pearson_result[0]),
                        'pearson_p': float(pearson_result[1]),
                        'n_samples': len(vwap_values),
                        'significant': pearson_result[1] < 0.05,
                        'description': 'VWAP absolute distance predicts day direction',
                    }

        # Test 4: VWAP distance helps when combined with first bar direction
        # Hypothesis: If first bar is far from VWAP, it's more likely to finish in that direction
        if (self.adr_strength.enabled and self.vwap_distance.enabled and
            'first_bar_direction' in df.columns and 'vwap_absolute_distance' in df.columns):
            combined_df = df[['first_bar_direction', 'vwap_absolute_distance', 'outcome']].dropna()
            combined_df = combined_df[combined_df['first_bar_direction'] != 0]
            
            if len(combined_df) > 1:
                combined_df = combined_df.copy()
                combined_df['first_bar_up'] = (combined_df['first_bar_direction'] > 0).astype(int)
                combined_df['direction_match'] = (combined_df['first_bar_up'] == combined_df['outcome']).astype(int)
                
                # Test: Does larger VWAP distance improve accuracy when direction matches?
                matched_df = combined_df[combined_df['direction_match'] == 1]
                if len(matched_df) > 1:
                    vwap_distances = matched_df['vwap_absolute_distance'].to_numpy()
                    outcomes = matched_df['outcome'].to_numpy()
                    
                    if len(set(vwap_distances)) > 1:
                        pearson_result = stats.pearsonr(vwap_distances, outcomes)
                        # Also calculate accuracy by distance quartiles
                        q25 = np.percentile(vwap_distances, 25)
                        q75 = np.percentile(vwap_distances, 75)
                        low_dist_accuracy = (outcomes[vwap_distances <= q25] == 1).sum() / len(outcomes[vwap_distances <= q25]) if len(outcomes[vwap_distances <= q25]) > 0 else 0
                        high_dist_accuracy = (outcomes[vwap_distances >= q75] == 1).sum() / len(outcomes[vwap_distances >= q75]) if len(outcomes[vwap_distances >= q75]) > 0 else 0
                        
                        correlations['vwap_distance_combined'] = {
                            'pearson_r': float(pearson_result[0]),
                            'pearson_p': float(pearson_result[1]),
                            'n_samples': len(vwap_distances),
                            'significant': pearson_result[1] < 0.05,
                            'low_distance_accuracy': float(low_dist_accuracy),
                            'high_distance_accuracy': float(high_dist_accuracy),
                            'description': 'VWAP distance effect when first bar direction matches day direction',
                        }

        # Prior Days Direction (isolated)
        if self.prior_days_dir.enabled and 'prior_days_ratio' in df.columns:
            prior_df = df[['prior_days_ratio', 'outcome']].dropna()
            if len(prior_df) > 1:
                prior_values = prior_df['prior_days_ratio'].to_numpy()
                prior_outcomes = prior_df['outcome'].to_numpy()

                if len(set(prior_values)) > 1 and len(set(prior_outcomes)) > 1:
                    pearson_result = stats.pearsonr(prior_values, prior_outcomes)
                    correlations['prior_days_ratio'] = {
                        'pearson_r': float(pearson_result[0]),
                        'pearson_p': float(pearson_result[1]),
                        'n_samples': len(prior_values),
                        'significant': pearson_result[1] < 0.05,
                        'description': 'Prior days direction ratio (isolated)',
                    }

        # Test 5: First bar direction + Prior days ratio (combined)
        if (self.adr_strength.enabled and self.prior_days_dir.enabled and
            'first_bar_direction' in df.columns and 'prior_days_ratio' in df.columns):
            combined_df = df[['first_bar_direction', 'prior_days_ratio', 'outcome']].dropna()
            combined_df = combined_df[combined_df['first_bar_direction'] != 0]
            
            if len(combined_df) > 1:
                combined_df = combined_df.copy()
                combined_df['first_bar_up'] = (combined_df['first_bar_direction'] > 0).astype(int)
                combined_df['direction_match'] = (combined_df['first_bar_up'] == combined_df['outcome']).astype(int)
                
                # Test: Does prior days ratio improve accuracy when first bar direction matches?
                matched_df = combined_df[combined_df['direction_match'] == 1]
                if len(matched_df) > 1:
                    prior_values = matched_df['prior_days_ratio'].to_numpy()
                    outcomes = matched_df['outcome'].to_numpy()
                    
                    if len(set(prior_values)) > 1:
                        pearson_result = stats.pearsonr(prior_values, outcomes)
                        correlations['prior_days_combined'] = {
                            'pearson_r': float(pearson_result[0]),
                            'pearson_p': float(pearson_result[1]),
                            'n_samples': len(prior_values),
                            'significant': pearson_result[1] < 0.05,
                            'description': 'Prior days ratio effect when first bar direction matches',
                        }

        # Test 6: First bar size + VWAP distance (combined)
        if (self.adr_strength.enabled and self.vwap_distance.enabled and
            'first_bar_size_pct_adr' in df.columns and 'vwap_absolute_distance' in df.columns):
            combined_df = df[['first_bar_direction', 'first_bar_size_pct_adr', 'vwap_absolute_distance', 'outcome']].dropna()
            combined_df = combined_df[combined_df['first_bar_direction'] != 0]
            
            if len(combined_df) > 1:
                combined_df = combined_df.copy()
                combined_df['first_bar_up'] = (combined_df['first_bar_direction'] > 0).astype(int)
                combined_df['direction_match'] = (combined_df['first_bar_up'] == combined_df['outcome']).astype(int)
                
                # Test: When direction matches, do size + VWAP distance together improve prediction?
                matched_df = combined_df[combined_df['direction_match'] == 1]
                if len(matched_df) > 1:
                    # Create interaction term: size * vwap_distance
                    matched_df = matched_df.copy()
                    matched_df['size_vwap_interaction'] = matched_df['first_bar_size_pct_adr'] * matched_df['vwap_absolute_distance']
                    
                    interaction_values = matched_df['size_vwap_interaction'].to_numpy()
                    outcomes = matched_df['outcome'].to_numpy()
                    
                    if len(set(interaction_values)) > 1:
                        pearson_result = stats.pearsonr(interaction_values, outcomes)
                        correlations['size_vwap_combined'] = {
                            'pearson_r': float(pearson_result[0]),
                            'pearson_p': float(pearson_result[1]),
                            'n_samples': len(interaction_values),
                            'significant': pearson_result[1] < 0.05,
                            'description': 'First bar size + VWAP distance interaction effect',
                        }

        # Test 7: All variables together (multivariate)
        # Use logistic regression to test cumulative effect of all variables
        if (self.adr_strength.enabled and self.vwap_distance.enabled and self.prior_days_dir.enabled and
            'first_bar_direction' in df.columns and 'first_bar_size_pct_adr' in df.columns and
            'vwap_absolute_distance' in df.columns and 'prior_days_ratio' in df.columns):
            
            from sklearn.linear_model import LogisticRegression
            from sklearn.metrics import accuracy_score, roc_auc_score
            
            # Prepare data
            multi_df = df[[
                'first_bar_direction', 'first_bar_size_pct_adr', 
                'vwap_absolute_distance', 'prior_days_ratio', 'outcome'
            ]].dropna()
            multi_df = multi_df[multi_df['first_bar_direction'] != 0]
            
            if len(multi_df) > 10:  # Need sufficient samples for regression
                multi_df = multi_df.copy()
                multi_df['first_bar_up'] = (multi_df['first_bar_direction'] > 0).astype(int)
                
                # Features: size, vwap_distance, prior_days_ratio, first_bar_up
                X = multi_df[['first_bar_size_pct_adr', 'vwap_absolute_distance', 
                              'prior_days_ratio', 'first_bar_up']].values
                y = multi_df['outcome'].values
                
                try:
                    # Fit logistic regression
                    model = LogisticRegression(max_iter=1000, random_state=42)
                    model.fit(X, y)
                    
                    # Predictions
                    y_pred = model.predict(X)
                    y_pred_proba = model.predict_proba(X)[:, 1]
                    
                    # Metrics
                    accuracy = accuracy_score(y, y_pred)
                    try:
                        auc = roc_auc_score(y, y_pred_proba)
                    except Exception:
                        auc = None
                    
                    # Compare to baseline (first bar direction only)
                    baseline_accuracy = (multi_df['first_bar_up'] == multi_df['outcome']).sum() / len(multi_df)
                    improvement = accuracy - baseline_accuracy
                    
                    correlations['all_variables_multivariate'] = {
                        'accuracy': float(accuracy),
                        'baseline_accuracy': float(baseline_accuracy),
                        'improvement': float(improvement),
                        'auc': float(auc) if auc is not None else None,
                        'n_samples': len(multi_df),
                        'coefficients': {
                            'first_bar_size': float(model.coef_[0][0]),
                            'vwap_distance': float(model.coef_[0][1]),
                            'prior_days_ratio': float(model.coef_[0][2]),
                            'first_bar_up': float(model.coef_[0][3]),
                        },
                        'description': 'All variables together (multivariate logistic regression)',
                    }
                except Exception as e:
                    # If regression fails, skip
                    pass

        return correlations

    def get_feasibility_score(self, correlations: dict) -> dict:
        """
        Calculate overall feasibility score based on correlations.
        
        Args:
            correlations: Dictionary from calculate_correlations
            
        Returns:
            Dictionary with feasibility scores and summary
        """
        if not correlations:
            return {
                'overall_score': 0.0,
                'message': 'No correlations calculated',
            }

        scores = []
        details = {}

        for condition_name, stats in correlations.items():
            # Handle different test formats
            if 'pearson_r' in stats:
                # Use absolute Pearson correlation value and significance
                # Handle NaN values
                if pd.isna(stats['pearson_r']):
                    continue
                    
                abs_pearson = abs(stats['pearson_r'])

                # Penalize if not significant
                if not stats['significant']:
                    abs_pearson *= 0.5

                # Scale to 0-100
                score = abs_pearson * 100

                scores.append(score)
                details[condition_name] = {
                    'score': score,
                    'correlation': abs_pearson,
                    'significant': stats['significant'],
                    'n_samples': stats['n_samples'],
                }
                if 'description' in stats:
                    details[condition_name]['description'] = stats['description']
                if 'accuracy' in stats:
                    details[condition_name]['accuracy'] = stats['accuracy']
            elif 'above_ma_accuracy' in stats:
                # For 5-day MA test, use accuracy difference as score
                accuracy_diff = abs(stats['accuracy_difference'])
                score = accuracy_diff * 100
                
                # Penalize if not significant
                if not stats.get('significant', False):
                    score *= 0.5
                
                scores.append(score)
                details[condition_name] = {
                    'score': score,
                    'above_ma_accuracy': stats['above_ma_accuracy'],
                    'below_ma_accuracy': stats['below_ma_accuracy'],
                    'accuracy_difference': stats['accuracy_difference'],
                    'significant': stats.get('significant', False),
                    'above_ma_n': stats['above_ma_n'],
                    'below_ma_n': stats['below_ma_n'],
                }
                if 'description' in stats:
                    details[condition_name]['description'] = stats['description']
            elif 'accuracy' in stats and 'baseline_accuracy' in stats:
                # For multivariate test, use improvement over baseline as score
                improvement = abs(stats['improvement'])
                score = improvement * 100
                
                scores.append(score)
                details[condition_name] = {
                    'score': score,
                    'accuracy': stats['accuracy'],
                    'baseline_accuracy': stats['baseline_accuracy'],
                    'improvement': stats['improvement'],
                    'n_samples': stats['n_samples'],
                }
                if 'description' in stats:
                    details[condition_name]['description'] = stats['description']
                if 'coefficients' in stats:
                    details[condition_name]['coefficients'] = stats['coefficients']

        overall_score = sum(scores) / len(scores) if scores else 0.0

        # Determine feasibility message
        if overall_score >= 70:
            message = 'High feasibility - strong correlations detected'
        elif overall_score >= 50:
            message = 'Moderate feasibility - some correlations detected'
        elif overall_score >= 30:
            message = 'Low feasibility - weak correlations'
        else:
            message = 'Very low feasibility - minimal correlations'

        return {
            'overall_score': overall_score,
            'message': message,
            'details': details,
        }


def main():
    """Example usage of the analyzer - scans last 1 year."""
    # Connect to IB
    ib = IB()
    try:
        ib.connect('127.0.0.1', 7497, clientId=99)
    except Exception as e:
        print(f'Failed to connect to IB: {e}')
        return

    # Create analyzer with all conditions enabled
    # Using 15 min bars, 15 minutes analysis, 3 prior days
    analyzer = OpenToCloseAnalyzer(
        ib=ib,
        adr_strength_enabled=True,
        vwap_distance_enabled=True,
        prior_days_enabled=True,
        n_minutes=15,
        bar_size='15 mins',
        prior_days=3,
        adr_days=20,
    )

    # Analyze a symbol over last 1 year (252 trading days)
    symbol = 'QQQ'
    print(f'Analyzing {symbol} over last 1 year (252 trading days)...')
    print('This may take several minutes...\n')

    results = analyzer.analyze_multiple_days(symbol, num_days=252)
    print(f'\nAnalyzed {len(results)} days')

    if not results:
        print('No results to analyze. Check IB connection and symbol.')
        ib.disconnect()
        return

    # Calculate correlations
    correlations = analyzer.calculate_correlations(results)
    print('\n' + '='*70)
    print('DIRECTIONAL HYPOTHESIS TEST RESULTS')
    print('='*70)
    for condition, stats in correlations.items():
        print(f'\n{condition.upper().replace("_", " ")}:')
        if stats.get('description'):
            print(f'  Test: {stats["description"]}')
        
        # Handle different test formats
        if 'pearson_r' in stats:
            print(f'  Pearson r:  {stats["pearson_r"]:7.4f}  (p-value: {stats["pearson_p"]:.4f})')
            significance = '✓ SIGNIFICANT' if stats['significant'] else '✗ Not significant'
            print(f'  Significance: {significance} (p < 0.05)')
            print(f'  Sample size: {stats["n_samples"]} days')
        elif 'above_ma_accuracy' in stats:
            # Special format for 5-day MA test
            print(f'  Open > 5-day MA accuracy: {stats["above_ma_accuracy"]*100:.2f}% (n={stats["above_ma_n"]})')
            print(f'  Open < 5-day MA accuracy: {stats["below_ma_accuracy"]*100:.2f}% (n={stats["below_ma_n"]})')
            print(f'  Accuracy difference: {stats["accuracy_difference"]*100:+.2f}%')
            if stats.get('p_value') is not None:
                print(f'  P-value: {stats["p_value"]:.4f}')
                significance = '✓ SIGNIFICANT' if stats['significant'] else '✗ Not significant'
                print(f'  Significance: {significance} (p < 0.05)')
        elif 'accuracy' in stats and 'baseline_accuracy' in stats:
            # Multivariate test format
            print(f'  Model accuracy: {stats["accuracy"]*100:.2f}%')
            print(f'  Baseline (first bar only): {stats["baseline_accuracy"]*100:.2f}%')
            print(f'  Improvement: {stats["improvement"]*100:+.2f}%')
            if stats.get('auc') is not None:
                print(f'  AUC-ROC: {stats["auc"]:.4f}')
            print(f'  Sample size: {stats["n_samples"]} days')
            if 'coefficients' in stats:
                print(f'  Coefficients:')
                for var, coef in stats['coefficients'].items():
                    print(f'    {var}: {coef:+.4f}')
        
        # Show accuracy if available (for other tests)
        if 'accuracy' in stats and 'pearson_r' in stats:
            print(f'  Accuracy: {stats["accuracy"]*100:.2f}% (direction matches outcome)')
        
        # Show distance-based accuracy if available
        if 'low_distance_accuracy' in stats and 'high_distance_accuracy' in stats:
            print(f'  Low VWAP distance accuracy:  {stats["low_distance_accuracy"]*100:.2f}%')
            print(f'  High VWAP distance accuracy: {stats["high_distance_accuracy"]*100:.2f}%')

    # Get feasibility score
    feasibility = analyzer.get_feasibility_score(correlations)
    print('\n' + '='*60)
    print('FEASIBILITY ASSESSMENT')
    print('='*60)
    print(f'Overall Score: {feasibility["overall_score"]:.2f}/100')
    print(f'Assessment: {feasibility["message"]}')
    
    if feasibility.get('details'):
        print('\nCondition Details:')
        for condition, detail in feasibility['details'].items():
            desc = detail.get('description', condition.replace('_', ' ').title())
            acc_str = f', accuracy: {detail["accuracy"]*100:.1f}%' if 'accuracy' in detail else ''
            print(f'  {desc}:')
            print(f'    Score: {detail["score"]:.2f}/100 '
                  f'(correlation: {detail["correlation"]:.4f}, '
                  f'significant: {detail["significant"]}, '
                  f'n={detail["n_samples"]}{acc_str})')

    ib.disconnect()


if __name__ == '__main__':
    main()
