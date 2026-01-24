"""Models for storing execution and order data."""
from django.db import models
from django.utils import timezone


class Execution(models.Model):
    """
    Stores execution/trade data from smb_screener.py and TWS.
    
    This model corresponds to the CSV structure:
    timestamp, trader, symbol, change_type, net_side, delta_magnitude,
    entry_price, stop_price, take_profit_price, order_id
    
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
    
    def __str__(self):
        return f"{self.timestamp} | {self.trader} | {self.symbol} | {self.change_type}"
