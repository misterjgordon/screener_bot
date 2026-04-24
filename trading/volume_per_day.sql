CREATE VIEW volume_per_day AS
SELECT
  b.*,
  SUM(b.volume) OVER (
    PARTITION BY DATE(b.timestamp)
    ORDER BY b.timestamp
    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
  ) AS volume_per_day
FROM bar b;
