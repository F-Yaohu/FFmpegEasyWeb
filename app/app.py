import os
from flask import Flask
from database import init_db
from routes.upload import upload_bp
from routes.convert import convert_bp
from routes.tasks import tasks_bp
from routes.misc import misc_bp
from routes.files import files_bp

app = Flask(__name__, static_folder='static', static_url_path='/static')
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-key')

app.register_blueprint(upload_bp)
app.register_blueprint(convert_bp)
app.register_blueprint(tasks_bp)
app.register_blueprint(misc_bp)
app.register_blueprint(files_bp)

init_db()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
