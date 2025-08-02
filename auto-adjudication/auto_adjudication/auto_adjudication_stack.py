from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    aws_s3 as s3,
    aws_lambda as _lambda,
    aws_apigatewayv2 as apigw,
    aws_apigatewayv2_integrations as integrations,
    aws_s3_deployment as s3deploy,
    SecretValue,
    aws_secretsmanager as secretsmanager,
    aws_s3_notifications as s3n,
    aws_iam as iam,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_ecr as ecr,
    aws_iam as iam,
    CfnOutput,
    aws_lambda_event_sources as lambda_event_sources,
    aws_sqs as sqs,
    aws_sns as sns, 
    aws_sns_subscriptions as subs,
)
from aws_cdk.aws_cloudfront import (
    Distribution,
    BehaviorOptions,
    ViewerProtocolPolicy,
    S3OriginAccessControl,
    Signing,
)
from aws_cdk.aws_cloudfront_origins import S3BucketOrigin
from aws_cdk.aws_iam import PolicyStatement, ServicePrincipal
from aws_cdk import CfnOutput
from constructs import Construct
import os

class AutoAdjudicationStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # 1) Private S3 bucket
        bucket = s3.Bucket(self, "GomspeedBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,

            cors=[
            s3.CorsRule(
                allowed_methods=[
                s3.HttpMethods.PUT,       # for the actual upload
                s3.HttpMethods.GET
                ],
                allowed_origins=[
                "https://d28sg3b1pfq095.cloudfront.net"  # your CloudFront domain
                ],
                allowed_headers=["*"],
                max_age=300
            )
            ]
        )

        
                # ——————————————————————————————————————————————
        #  2) Store the OpenAI key in Secrets Manager
        # ——————————————————————————————————————————————
        openai_api_key = self.node.try_get_context("openaiApiKey") or os.getenv("OPENAI_API_KEY")
        if not openai_api_key:
            raise ValueError("Provide your OpenAI key via `cdk deploy -c openaiApiKey=sk-…` or env OPENAI_API_KEY")

        openai_secret = secretsmanager.Secret(self, "OpenAIApiKeySecret",
            secret_name="openai/api-key",
            description="OpenAI API key for claim-checker",
            secret_object_value={
                "OPENAI_API_KEY": SecretValue.plain_text(openai_api_key)
            }
        )


                # ───────────────────────────────────────────────────
        # 3) VPC + EC2-backed ECS cluster + Task Definition
        # ───────────────────────────────────────────────────

        # 3.1) Create a VPC (2 AZs)
        vpc = ec2.Vpc(self, "Vpc", max_azs=2)

        # 3.2) ECS cluster on that VPC
        cluster = ecs.Cluster(self, "EcsCluster", vpc=vpc)

        # 3.3) Add EC2 capacity (t2.micro only)
        cluster.add_capacity("DefaultAutoScalingGroup",
            instance_type=ec2.InstanceType.of(ec2.InstanceClass.T2, ec2.InstanceSize.MICRO),
            desired_capacity=1,
            min_capacity=1,
            max_capacity=3  # scale up to 3 instances if CPU pressure demands
        )

        # 3.4) IAM roles for ECS
        # Execution role (pull image + write logs)
        execution_role = iam.Role(self, "EcsExecutionRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                )
            ]
        )
        # Task role (your container’s permissions)
        task_role = iam.Role(self, "EcsTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com")
        )
        # grant it read access to your S3 bucket
        bucket.grant_read(task_role)

        # 3.5) Define the EC2 Task Definition
        task_def = ecs.Ec2TaskDefinition(self, "ClaimCheckerTaskDef",
            execution_role=execution_role,
            task_role=task_role
        )

        # 3.6) Point at your ECR image (assumes you've pushed to an ECR repo named "claim-checker")
        repo = ecr.Repository.from_repository_name(self, "ClaimCheckerRepo", "claim-checker")

        container = task_def.add_container("claim-checker",
            image=ecs.ContainerImage.from_ecr_repository(repo, "1.0.3"),
            cpu=256,
            memory_limit_mib=512,
            logging=ecs.LogDrivers.aws_logs(stream_prefix="claim-checker")
        )

        # 3.7) Expose container port if needed (uncomment if your app listens)
        # container.add_port_mappings(ecs.PortMapping(container_port=80))

        # 3.8) Export outputs so you can verify
        CfnOutput(self, "EcsClusterName",
            value=cluster.cluster_name,
            description="Name of the ECS cluster"
        )
        CfnOutput(self, "TaskDefArn",
            value=task_def.task_definition_arn,
            description="ARN of the ECS task definition"
        )



                # ─────────────────────────────────────────────────
        # 4) ECS‐Runner Lambda + SQS queue + S3 notification
        # ─────────────────────────────────────────────────

        # 4.1) The Lambda that will kick off ECS tasks
        ecs_runner = _lambda.Function(self, "EcsRunnerFunction",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="ecs_runner.lambda_handler",
            code=_lambda.Code.from_asset("lambda/ecs_runner"),
            timeout=Duration.seconds(30),
            environment={
            "SECRET_ARN":     openai_secret.secret_arn,
            "CLUSTER_NAME":   cluster.cluster_name,
            "TASK_DEFINITION": task_def.task_definition_arn,
            "CONTAINER_NAME":  container.container_name,
            }
        )

        # allow it to read the secret
        openai_secret.grant_read(ecs_runner)

        # allow it to start ECS tasks and pass the task role
        ecs_runner.add_to_role_policy(iam.PolicyStatement(
            actions=["ecs:RunTask","iam:PassRole"],
            resources=[ task_def.task_definition_arn,
                        execution_role.role_arn,
                        task_role.role_arn ]
        ))


                # Allow the runner to inspect ECS tasks & definitions
        ecs_runner.add_to_role_policy(iam.PolicyStatement(
            actions=[
                "ecs:DescribeTasks",
                "ecs:ListTasks",
                "ecs:DescribeTaskDefinition"
            ],
            resources=["*"]  # you can narrow this to your TaskDef ARN and Cluster ARN if you like
        ))



                # Allow the runner Lambda to describe and list ECS tasks
        ecs_runner.add_to_role_policy(iam.PolicyStatement(
            actions=[
                "ecs:DescribeTasks",
                "ecs:ListTasks"
            ],
            resources=["*"]  # or scope to your cluster ARN if you prefer
        ))

                # Allow reading logs to fetch snippets
        ecs_runner.add_to_role_policy(iam.PolicyStatement(
            actions=[
                "logs:DescribeLogStreams",
                "logs:GetLogEvents"
            ],
            resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:*"]
        ))





        
                # 4.5) SNS topic for notifications
        notify_topic = sns.Topic(self, "ClaimResultTopic",
            display_name="Claim result notifications"
        )

        # Email subscription
        notify_topic.add_subscription(subs.EmailSubscription("gomezoluwatobi@gmail.com"))

        # Give your runner Lambda permission to publish
        ecs_runner.add_environment("NOTIFY_TOPIC_ARN", notify_topic.topic_arn)
        notify_topic.grant_publish(ecs_runner)



        # 4.2) SQS queue that batches uploads
        uploads_queue = sqs.Queue(self, "UploadsQueue",
            visibility_timeout=Duration.minutes(5),
            receive_message_wait_time=Duration.seconds(20),
        )


                # Allow S3 to send messages to this queue
        uploads_queue.add_to_resource_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            principals=[iam.ServicePrincipal("s3.amazonaws.com")],
            actions=["sqs:SendMessage"],
            resources=[uploads_queue.queue_arn],
            conditions={
                "ArnLike": {"aws:SourceArn": bucket.bucket_arn}
            }
        ))


        # 4.3) S3 → SQS (for your upload prefix)
        bucket.add_event_notification(
            s3.EventType.OBJECT_CREATED,
            s3n.SqsDestination(uploads_queue)
        )

        # 4.4) SQS → Lambda (batch of 2)
        ecs_runner.add_event_source(lambda_event_sources.SqsEventSource(
            uploads_queue,
            batch_size=1,
            max_batching_window=Duration.seconds(30)
        ))




        # 5) Allow CloudFront’s Origin Access Control to GET objects
        bucket.add_to_resource_policy(PolicyStatement(
            actions=["s3:GetObject"],
            resources=[f"{bucket.bucket_arn}/*"],
            principals=[ServicePrincipal("cloudfront.amazonaws.com")]
        ))

        

        # 4) Create the Origin Access Control for this S3 origin
        oac = S3OriginAccessControl(self, "GomspeedOAC",
            signing=Signing.SIGV4_ALWAYS   # sign all requests
        )  # :contentReference[oaicite:0]{index=0}

        # 5) Build a CloudFront origin that uses that OAC
        s3_origin = S3BucketOrigin.with_origin_access_control(bucket,
            origin_access_control=oac,
            origin_path="/static-website001gomspeed"
        )  # :contentReference[oaicite:1]{index=1}

        # 6) CloudFront distribution
        distribution = Distribution(self, "GomspeedCloudFront",
            default_root_object="index.html",
            default_behavior=BehaviorOptions(
                origin=s3_origin,
                viewer_protocol_policy=ViewerProtocolPolicy.REDIRECT_TO_HTTPS
            )
        )

        # 7) Deploy your static site under the prefix
        s3deploy.BucketDeployment(self, "DeployWebsite",
            sources=[s3deploy.Source.asset("website")],
            destination_bucket=bucket,
            destination_key_prefix="static-website001gomspeed",
            distribution=distribution,        # invalidate CloudFront
            distribution_paths=["/*"],        # every path
        )

        # … after distribution is created …
        CfnOutput(self, "CloudFrontUrl",
            value=distribution.distribution_domain_name,
            description="CloudFront distribution domain name"
        )


        # ✅ Lambda function (for generating presigned URLs)
        upload_lambda = _lambda.Function(self, "PresignUploadLambda",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="upload.handler",
            code=_lambda.Code.from_asset("lambda"),
            environment={
                "BUCKET_NAME": bucket.bucket_name
            },
            timeout=Duration.seconds(10)
        )

        # ✅ Permissions
        bucket.grant_put(upload_lambda)

        # ✅ API Gateway (for invoking Lambda)
        http_api = apigw.HttpApi(self, "PresignHttpAPI",
            cors_preflight=apigw.CorsPreflightOptions(
                allow_headers=["*"],
                allow_methods=[apigw.CorsHttpMethod.GET],
                allow_origins=["*"],  # Replace with your domain later
            )
        )

        http_api.add_routes(
            path="/presigned-url",
            methods=[apigw.HttpMethod.GET],
            integration=integrations.HttpLambdaIntegration("LambdaIntegration", upload_lambda)
        )

                # … after http_api is created …
        CfnOutput(self, "ApiUrl",
            value=http_api.url,
            description="HTTP API endpoint for presigned URLs"
        )


        # ✅ Lifecycle rule (ensure claimcollectors11/ prefix exists)
        bucket.add_lifecycle_rule(
            prefix="claimcollectors11/",
            enabled=True,
            expiration=Duration.days(30)  # ✅ Files older than 30 days will be deleted
        )

        # ✅ Output CloudFront & API URLs
        self.cloudfront_url = distribution.distribution_domain_name
        self.api_url = http_api.url

        

        