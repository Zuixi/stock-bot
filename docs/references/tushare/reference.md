## 如何优雅高效的撸数据？

---

获取Tushare Pro 的数据API，首先需要注册一个pro账号，然后登录pro网站在个人主页里拿到token码。另外，别忘了修改一下个人信息，这样可以多20积分。对于股票行情数据，只要有120积分就可以相对高频的撸数据了，这120积分随手可得（注册成功有100积分、然后修改个人信息有20积分）。

Tushare的行情等时间序列数据，一般都有两个常用参数： **trade_date** 和 **ts_code** ，分别是交易日期和证券代码。如果你是想提取部分个股的历史数据，用ts_code参数，加上开始和结束日期可以方便提取数据。

但！如果是要获取所有历史数据，我们不建议通过ts_code来循环，而是用trade_date来提取，道理很简单，股票有5000多个，需要循环5000多次，每年的交易日也就才220左右，所以效率更高。总的来说，积分越高可以调取的频次会越高。

也就是以下方式：

```
import tushare as ts

pro = ts.pro_api()

df = pro.daily(trade_date='20200325')
```

在循环提取数据时，首先我们可以通过交易日历拿到一段历史的交易日。

```
#获取20200101～20200401之间所有有交易的日期
df = pro.trade_cal(exchange='SSE', is_open='1', 
                            start_date='20200101', 
                            end_date='20200401', 
                            fields='cal_date')
    
 print(df.head())
```

交易日：

```
cal_date
0  20200102
1  20200103
2  20200106
3  20200107
4  20200108
```

循环过程中，为了保持数据提取的稳定性，可以先建立一个专门的函数，实现一个重试机制：

```
def get_daily(self, ts_code='', trade_date='', start_date='', end_date=''):
    for _ in range(3):
      try:
            if trade_date:
                df = self.pro.daily(ts_code=ts_code, trade_date=trade_date)
            else:
                df = self.pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
         except:
                time.sleep(1)
        else:
                return df
```

然后通过在循环中调取数据：

```
for date in df['cal_date'].values:
     df = get_daily(date)
```