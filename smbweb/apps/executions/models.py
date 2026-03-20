"""Models for storing execution and order data."""
from django.db import models
from django.utils import timezone


class Execution(models.Model):
    """
    Stores execution/trade data from smb_screener.py and TWS.

    This model corresponds to the CSV structure:
    timestamp, trader, symbol, change_type, net_side, delta_magnitude,
    entry_price, stop_price, take_profit_price, order_id, filled_price,
    shares, total_risk, risk_per_share, market_value, risk_%

    Future: Will also include data directly from TWS orders and executions.
    """

    # Timestamp when the execution occurred
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)

    # Trader name (e.g., "Justin Spero", "Jeff Holden")
    trader = models.CharField(max_length=100, db_index=True)

    # Stock symbol/ticker (e.g., "AAPL", "MSFT")
    symbol = models.CharField(max_length=20, db_index=True)

    # Change type: NEW, ADD, TRIM, CLOSE, FLIP
    CHANGE_TYPE_CHOICES = [
        ('NEW', 'New Position'),
        ('ADD', 'Add to Position'),
        ('TRIM', 'Trim Position'),
        ('CLOSE', 'Close Position'),
        ('FLIP', 'Flip Position'),
    ]
    change_type = models.CharField(max_length=10, choices=CHANGE_TYPE_CHOICES, db_index=True)

    # Position side: long, short, flat
    SIDE_CHOICES = [
        ('long', 'Long'),
        ('short', 'Short'),
        ('flat', 'Flat'),
    ]
    net_side = models.CharField(max_length=10, choices=SIDE_CHOICES)

    # Change in magnitude (position size change)
    delta_magnitude = models.FloatField()

    # Magnitude (position size) - for future use
    magnitude = models.FloatField(null=True, blank=True)

    # Optional price fields
    entry_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    stop_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    take_profit_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # IB order ID if order was placed
    order_id = models.CharField(max_length=50, null=True, blank=True, db_index=True)
    filled_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # Risk/sizing from calculate_num_shares_from_risk
    shares = models.IntegerField(null=True, blank=True)
    total_risk = models.FloatField(null=True, blank=True)
    risk_per_share = models.FloatField(null=True, blank=True)
    market_value = models.FloatField(null=True, blank=True)
    risk_percent = models.FloatField(null=True, blank=True)

    # Future: TWS-specific fields can be added here
    # tws_execution_id = models.CharField(max_length=50, null=True, blank=True)
    # tws_order_id = models.IntegerField(null=True, blank=True)

    class Meta:
        db_table = 'executions'
        ordering = ['-timestamp']  # Most recent first
        indexes = [
            models.Index(fields=['-timestamp', 'trader']),
            models.Index(fields=['symbol', '-timestamp']),
            models.Index(fields=['change_type', '-timestamp']),
            models.Index(fields=['trader', 'symbol', '-timestamp']),
        ]

    def __str__(self) -> str:
        return f'{self.timestamp} | {self.trader} | {self.symbol} | {self.change_type}'


class Position(models.Model):
    """
    Event log for position changes - stores change events for all traders (equity + options).

    This is an event log approach: each record represents a CHANGE event, not a full snapshot.
    Only rows with changes (delta_magnitude != 0 or change_type != null) are saved.

    Data is read from position_snapshot.json file and only changed positions are saved.

    Think of it like a cumulative sum:
    - Each record = one change event
    - total_magnitude = position size AFTER this change
    - delta_magnitude = the change that occurred (+ or -)
    - prev_magnitude = position size BEFORE this change

    To reconstruct current state:
    - Query latest record per (trader, symbol) to get current position
    - Query all records per (trader, symbol) ordered by timestamp to see full history

    This approach:
    - Saves only when positions change (not every cycle)
    - Works for all traders regardless of TRADER_ENABLED status
    - Captures both equity and options positions
    - Minimizes data volume (~100-1000 records/day vs ~350K if saving all)
    """

    # Timestamp when the change occurred
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)

    # Trader name (e.g., "Justin Spero", "Jeff Holden", "Steve Spencer", "Kenneth Sharkness")
    trader = models.CharField(max_length=100, db_index=True)

    # Symbol (e.g., "AAPL", "AAPL 2026-01-23 C 150.00")
    symbol = models.CharField(max_length=100, db_index=True)

    # Instrument type: equity or option
    INSTRUMENT_TYPE_CHOICES = [
        ('equity', 'Equity'),
        ('option', 'Option'),
    ]
    instrument_type = models.CharField(max_length=10, choices=INSTRUMENT_TYPE_CHOICES, db_index=True)

    # Underlying symbol (same as symbol for equity, underlying stock for options)
    underlying = models.CharField(max_length=20, db_index=True)

    # Option-specific fields (null for equity)
    expiry = models.DateField(null=True, blank=True, db_index=True)
    strike = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    option_type = models.CharField(max_length=1, null=True, blank=True, choices=[('C', 'Call'), ('P', 'Put')])

    # Position details
    is_long_term = models.BooleanField(default=False, db_index=True)  # pyright: ignore[reportArgumentType]
    net_side = models.CharField(max_length=10, choices=Execution.SIDE_CHOICES)  # long, short, flat
    conflict = models.BooleanField(default=False)  # pyright: ignore[reportArgumentType]

    # Magnitude values
    total_magnitude = models.FloatField(default=0.0)  # pyright: ignore[reportArgumentType]
    prev_magnitude = models.FloatField(null=True, blank=True)
    delta_magnitude = models.FloatField(null=True, blank=True)

    # Change type (NEW, ADD, TRIM, CLOSE, FLIP, or null if no change)
    change_type = models.CharField(
        max_length=10,
        choices=Execution.CHANGE_TYPE_CHOICES + [('', 'No Change')],
        null=True,
        blank=True,
        db_index=True
    )

    class Meta:
        db_table = 'positions'
        # Order by: trader, underlying, magnitude (descending)
        ordering = ['trader', 'underlying', '-total_magnitude']
        indexes = [
            models.Index(fields=['-timestamp', 'trader']),
            models.Index(fields=['trader', 'underlying', '-total_magnitude']),  # Matches default ordering
            models.Index(fields=['trader', 'underlying', '-timestamp']),
            models.Index(fields=['instrument_type', '-timestamp']),
            models.Index(fields=['timestamp', 'trader', 'symbol']),  # For uniqueness checks
        ]
        # Prevent duplicate change events for same timestamp/trader/symbol
        unique_together = [['timestamp', 'trader', 'symbol']]

    def __str__(self) -> str:
        return f'{self.timestamp} | {self.trader} | {self.symbol} | {self.net_side} | {self.total_magnitude}'
