import sqlalchemy as sal
from sqlalchemy.ext.automap import automap_base
from sqlalchemy import MetaData, func
import os

#Define database engine
URI = "postgresql://" + os.environ["POSTGRES_USER"] + \
                          ":" + os.environ["POSTGRES_PASSWORD"] + "@" + os.environ["POSTGRES_HOST"] + \
                          ":" + os.environ["POSTGRES_PORT"] + "/" + os.environ["POSTGRES_DB"]
engine = sal.create_engine(URI)

#Define database model
metadata_public = MetaData(schema="fundamentals")
metadata_public.reflect(engine)
inc_schema = automap_base(metadata=metadata_public)
inc_schema.prepare()

#Define tables
income_table = inc_schema.classes.income_annual