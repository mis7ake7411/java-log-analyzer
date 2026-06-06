# Java Log 分析報告

## 統計摘要
| 日誌等級 | 數量 |
| --- | --- |
| ERROR | 1 |
| INFO | 2 |
| WARN | 1 |

## 錯誤詳情
### 錯誤 1: Database connection failed
- **時間**: 2026-06-06 10:10:00.555
- **等級**: ERROR
#### 堆疊追蹤 (Stacktrace):
```java
java.sql.SQLException: Connection refused
    at com.example.db.Connector.connect(Connector.java:45)
    at com.example.Service.getData(Service.java:12)
```

---
