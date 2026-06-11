from flask import Flask
from flask_restful import Api

from config import secrets
from api.routes import ProcessOrder, CleanCache

app = Flask(__name__)
app.config.from_object(secrets)

api = Api(app)
api.add_resource(ProcessOrder, "/process")
api.add_resource(CleanCache, "/clean-cache")

if __name__ == "__main__":
    app.run(debug=False)
