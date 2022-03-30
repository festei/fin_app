FROM python:3.8

WORKDIR /app

COPY ./requirements.txt /app/requirements.txt

RUN pip install --upgrade pip
RUN pip install --no-cache-dir --upgrade -r /app/requirements.txt

COPY ./api .

ENV POSTGRES_USER=qssgcdzllnnlgp
ENV POSTGRES_PASSWORD=9708f13bd37a6dac0036a783d7cd822b2e4c744e8be6733b2afc71d2eb5ca33d
ENV POSTGRES_DB=dfs3g3gnqoftrg
ENV POSTGRES_HOST=ec2-3-217-251-77.compute-1.amazonaws.com
ENV POSTGRES_PORT=5432

CMD [ "python", "./main.py"]