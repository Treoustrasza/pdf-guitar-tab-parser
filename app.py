"""
Flask 后端：PDF 吉他六线谱 → MusicXML 在线转换服务
"""

import os
import uuid
import threading
from flask import Flask, request, jsonify, send_file, render_template
from tab_parser import convert_pdf_to_musicxml

app = Flask(__name__)

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
OUTPUT_FOLDER = os.path.join(os.path.dirname(__file__), 'outputs')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# 任务状态存储（内存，单机够用）
tasks = {}


def run_conversion(task_id, pdf_path, output_path, title, tempo):
    try:
        tasks[task_id]['status'] = 'processing'
        convert_pdf_to_musicxml(
            pdf_path,
            output_path=output_path,
            title=title,
            tempo=tempo,
            also_save_ascii=True,
        )
        tasks[task_id]['status'] = 'done'
        tasks[task_id]['musicxml'] = output_path
        tasks[task_id]['ascii'] = os.path.splitext(output_path)[0] + '_debug.txt'
    except Exception as e:
        tasks[task_id]['status'] = 'error'
        tasks[task_id]['error'] = str(e)
    finally:
        # 清理上传的 PDF
        if os.path.exists(pdf_path):
            os.remove(pdf_path)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/convert', methods=['POST'])
def convert():
    if 'file' not in request.files:
        return jsonify({'error': '没有上传文件'}), 400

    f = request.files['file']
    if not f.filename.lower().endswith('.pdf'):
        return jsonify({'error': '只支持 PDF 文件'}), 400

    tempo = int(request.form.get('tempo', 100))
    title = request.form.get('title', '') or os.path.splitext(f.filename)[0]

    task_id = str(uuid.uuid4())
    pdf_path = os.path.join(UPLOAD_FOLDER, f'{task_id}.pdf')
    output_path = os.path.join(OUTPUT_FOLDER, f'{task_id}.musicxml')

    f.save(pdf_path)

    tasks[task_id] = {'status': 'pending'}

    t = threading.Thread(
        target=run_conversion,
        args=(task_id, pdf_path, output_path, title, tempo),
        daemon=True,
    )
    t.start()

    return jsonify({'task_id': task_id})


@app.route('/api/status/<task_id>')
def status(task_id):
    task = tasks.get(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404
    return jsonify({'status': task['status'], 'error': task.get('error', '')})


@app.route('/api/download/<task_id>/<fmt>')
def download(task_id, fmt):
    task = tasks.get(task_id)
    if not task or task['status'] != 'done':
        return jsonify({'error': '文件未就绪'}), 404

    if fmt == 'musicxml':
        path = task.get('musicxml')
        mimetype = 'application/vnd.recordare.musicxml+xml'
        suffix = '.musicxml'
    elif fmt == 'ascii':
        path = task.get('ascii')
        mimetype = 'text/plain'
        suffix = '_debug.txt'
    else:
        return jsonify({'error': '不支持的格式'}), 400

    if not path or not os.path.exists(path):
        return jsonify({'error': '文件不存在'}), 404

    return send_file(path, mimetype=mimetype,
                     as_attachment=True,
                     download_name=f'output{suffix}')


if __name__ == '__main__':
    app.run(debug=True, port=5000)
