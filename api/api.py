import os
import sys
import uuid

from plan_gen import plan_gen
from premise_gen import premise_gen
from story_gen import story_gen
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flask import Flask, request, jsonify

from datetime import datetime

app = Flask(__name__)

premise_gen = premise_gen()
plan_gen = plan_gen()
story_gen = story_gen()

@app.route('/story/v1/premis', methods=['POST'])
def premis():
    prompt = request.json.get('prompt')
    job_id = request.json.get('job_id')

    current_time = datetime.now()
    time_stamp = current_time.strftime('%Y-%m-%d %H:%M:%S')
    print(f"get premis request: {time_stamp} - {job_id}")

    premise_gen.task_status[job_id] = {
        "job_id": job_id,
        "status": "processing",
        "gen_type": "premise",
        "results": {
            "premise": ""
        }
    }
    premise_gen.generation_queue(job_id, prompt, "premise")
    return jsonify(premise_gen.task_status[job_id])

@app.route('/story/v1/plan', methods=['POST'])
def plan():
    job_id = request.json.get('job_id')

    current_time = datetime.now()
    time_stamp = current_time.strftime('%Y-%m-%d %H:%M:%S')
    print(f"get plan request: {time_stamp} - {job_id}")
    
    plan_gen.task_status[job_id] = {
        "job_id": job_id,
        "status": "processing",
        "gen_type": "plan",
        "results": {
            "plan": ""
        }
    }
    plan_gen.generation_queue(job_id, "plan")
    return jsonify(plan_gen.task_status[job_id])

@app.route('/story/v1/plan/plots', methods=['POST'])
def extend_plots():
    job_id = request.json.get('job_id')

    current_time = datetime.now()
    time_stamp = current_time.strftime('%Y-%m-%d %H:%M:%S')
    print(f"list_extend_plots request :{time_stamp} - {job_id}")
    plots = plan_gen.list_extend_plots(job_id)

    if len(plots) == 0:
        return jsonify({
            "job_id": job_id,
            "status": "failed",
            "gen_type": "extend_plots",
            "results": {
                "plots": []
            }
        })

    return jsonify({
        "job_id": job_id,
        "status": "success",
        "gen_type": "extend_plots",
        "results": {
            "plots": plots
        }
    })

@app.route('/story/v1/story', methods=['POST'])
def story():
    job_id = request.json.get('job_id')

    current_time = datetime.now()
    time_stamp = current_time.strftime('%Y-%m-%d %H:%M:%S')
    print(f"get story request :{time_stamp} - {job_id}")
    story_gen.task_status[job_id] = {
        "job_id": job_id,
        "status": "processing",
        "gen_type": "story",
        "results": {
            "story": ""
        }
    }
    story_gen.generation_queue(job_id, "story")
    return jsonify(story_gen.task_status[job_id])

@app.route('/story/v1/story/extend', methods=['POST'])
def extend_story():
    job_id = request.json.get('job_id')
    plot = request.json.get('plot')
    extend_id = request.json.get('extend_id')

    current_time = datetime.now()
    time_stamp = current_time.strftime('%Y-%m-%d %H:%M:%S')
    print(f"extend_story request :{time_stamp} - {job_id}")
    
    story_gen.task_status[job_id] = {
        "job_id": job_id,
        "extend_id": extend_id,
        "status": "processing",
        "gen_type": "extend_story",
        "results": {
            "story": ""
        }
    }
    
    plan_gen.plan_extend(job_id, plot)
    story_gen.generation_queue(job_id, "extend_story", extend_id)
    return jsonify(story_gen.task_status[job_id])

    # plan_gen.extend_by_plot(job_id, plot)

    # story_gen.story_extend_by_plot(job_id)

    # return jsonify({
    #     "job_id": job_id,
    #     "status": "success",
    #     "gen_type": "extend_story",
    #     "results": {
    #         "story": ""
    #     }
    # })

@app.route('/story/v1/jobs_status', methods=['POST'])
def jobs_status():
    statuses = []
    job_ids = request.json.get('job_ids', [])
    gen_type = request.json.get('gen_type')

    if not isinstance(job_ids, list) or len(job_ids) == 0:
        return jsonify(
            {
                "job_id": "None",
                "status": "not_found",
                "gen_type": gen_type,
                "results": {
                    "premise": "",
                    "plan": "",
                    "story": ""
                }
            })
    
    for job_id in job_ids:
        if gen_type == "premise":
            status = premise_gen.task_status.get(job_id, {
                "job_id": job_id,
                "status": "not_found",
                "gen_type": "premise",
                "results": {
                    "premise": ""
                }
            })
        elif gen_type == "plan":
            status = plan_gen.task_status.get(job_id, {
                "job_id": job_id,
                "status": "not_found",
                "gen_type": "plan",
                "results": {
                    "plan": ""
                }
            })
        elif gen_type == "story":
            status = story_gen.task_status.get(job_id, {
                "job_id": job_id,
                "status": "not_found",
                "gen_type": "story",
                "results": {
                    "story": ""
                }
            })
        elif gen_type == "extend_story":
            status = story_gen.task_status.get(job_id, {
                "job_id": job_id,
                "status": "not_found",
                "gen_type": "extend_story",
                "results": {
                    "story": ""
                }
            })
        
        #if status["status"] == "not_found":
        # ToDo: check file exists or not

        statuses.append(status)
    return jsonify(statuses)

# health check
@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        "status": "alive"
    })

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=6006)
