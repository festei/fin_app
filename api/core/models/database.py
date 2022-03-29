import sqlalchemy as sal
from sqlalchemy.ext.automap import automap_base
from sqlalchemy import MetaData, func

#Define database engine
URI = 'postgresql://fvzzihomffinqi:a7ff18d0ceea4d8af30ad33235e35c3b216cb411f15dfe935c36aea5e3de2c17@ec2-34-231-63-30.compute-1.amazonaws.com:5432/d28du7j6uaiqdg'
engine = sal.create_engine(URI)

#Define database model
metadata_public = MetaData(schema="fundamentals")
metadata_public.reflect(engine)
inc_schema = automap_base(metadata=metadata_public)
inc_schema.prepare()

#Define tables
income_table = inc_schema.classes.income_annual