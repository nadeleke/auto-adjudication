import os
import json
import time
import boto3

sm   = boto3.client("secretsmanager")
ecs  = boto3.client("ecs")
logs = boto3.client("logs")
sns  = boto3.client("sns")

SECRET_ARN      = os.environ["SECRET_ARN"]
CLUSTER_NAME    = os.environ["CLUSTER_NAME"]
TASK_DEFINITION = os.environ["TASK_DEFINITION"]
CONTAINER_NAME  = os.environ["CONTAINER_NAME"]
TOPIC_ARN       = os.environ["NOTIFY_TOPIC_ARN"]  # add this in CDK

def get_api_key():
    resp = sm.get_secret_value(SecretId=SECRET_ARN)
    s = resp["SecretString"]
    try:
        return json.loads(s)["OPENAI_API_KEY"]
    except:
        return s

def lambda_handler(event, context):
    print("EVENT:", json.dumps(event))
    api_key = get_api_key()
    for msg in event.get("Records", []):
        body = msg.get("body", "")
        print("RAW BODY:", body)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            continue

        # unwrap S3 event
        s3_records = None
        if "Records" in payload:
            s3_records = payload["Records"]
        elif "Message" in payload:
            try:
                inner = json.loads(payload["Message"])
                s3_records = inner.get("Records", [])
            except:
                continue
        if not s3_records:
            continue

        for rec in s3_records:
            bucket = rec["s3"]["bucket"]["name"]
            key    = rec["s3"]["object"]["key"]
            print(f"Launching task for s3://{bucket}/{key}")

            run = ecs.run_task(
                cluster=CLUSTER_NAME,
                taskDefinition=TASK_DEFINITION,
                launchType="EC2",
                overrides={"containerOverrides":[
                    {"name": CONTAINER_NAME,
                     "environment":[
                         {"name":"OPENAI_API_KEY","value": api_key},
                         {"name":"S3_BUCKET",      "value": bucket},
                         {"name":"S3_KEY",         "value": key},
                     ]}
                ]},
                count=1
            )
            task_arn = run["tasks"][0]["taskArn"]

            # Wait for the task to stop
            waiter = ecs.get_waiter("tasks_stopped")
            waiter.wait(cluster=CLUSTER_NAME, tasks=[task_arn])

            # Describe to get exit code
            desc = ecs.describe_tasks(cluster=CLUSTER_NAME, tasks=[task_arn])
            exit_code = desc["tasks"][0]["containers"][0]["exitCode"]
            status    = "✅ Accepted" if exit_code == 0 else "❌ Rejected/Errored"

            # Fetch last few log lines for context
            # 1) fetch exit code as before
            desc = ecs.describe_tasks(cluster=CLUSTER_NAME, tasks=[task_arn])
            exit_code = desc["tasks"][0]["containers"][0]["exitCode"]

            # 2) fetch the TaskDefinition’s logConfiguration
            td = ecs.describe_task_definition(taskDefinition=TASK_DEFINITION)
            cd = td["taskDefinition"]["containerDefinitions"][0]
            opts = cd["logConfiguration"]["options"]
            log_group     = opts["awslogs-group"]
            stream_prefix = opts["awslogs-stream-prefix"]

            # 3) build the real log stream name
            task_id    = task_arn.rsplit("/", 1)[1]
            log_stream = f"{stream_prefix}/{CONTAINER_NAME}/{task_id}"

            # 4) pull the last few log events
            events = logs.get_log_events(
                logGroupName=log_group,
                logStreamName=log_stream,
                limit=10,
                startFromHead=False
            )["events"]
            snippet = "\n".join(e["message"] for e in events)

            # 5) publish result to SNS
            sns = boto3.client("sns")
            subject = "Claim Processor: " + ("✅ Accepted" if exit_code == 0 else "❌ Rejected/Error")
            body = f"""\
            S3://{bucket}/{key}
            Exit code: {exit_code}

            Last logs:
            {snippet}
            """
            sns.publish(TopicArn=os.environ["NOTIFY_TOPIC_ARN"], Subject=subject, Message=body)


