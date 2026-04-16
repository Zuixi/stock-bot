如果数据需要长期使用，尤其是历史数据，我们建议，可以将提取到的数据存入本地数据库，例如MySQL。

有如下几个步骤：

安装依赖包
sqlalchemy、mysqlclient
安装MySQL
对于MySQL版本，没有特别的要求，mysql 5+、mysql 8+ 都可以，如果是最新版mysql，需要将sqlalchemy升级到最新版。具体的安装过程，这里不做介绍，大家可自行baidu，有很多参考材料。
编写入库代码
由于用了sqlalchemy，这个过程非常简单。用户无需首先在数据库中建表就可以执行数据入库，但这种默认方式所创建的数据表并不是最优的数据结构，可以参考第4条进行优化。

res = df.to_sql('stock_basic', engine_ts, index=False, if_exists='append', chunksize=5000)

数据结构优化
对于默认创建的表，会有一些不太符合实际应用，比如数据结构类型比较单一，没有主键和索引等约束，没有comments等等。我们可以在数据库客户端对所建立的表进行修改，使其符合实际的最优设计。比如将一般的str类型转成varchar2数据类型，而不是text数据类型。

实现本地调度程序
完成数据调取接口和入库程序之后，我们可以开发一个调取程序，可以让系统的调度系统来定时从tushare拉取数据。比如windows我们可以用计划任务，Linux可以使用crontab。