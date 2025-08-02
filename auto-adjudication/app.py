#!/usr/bin/env python3
import os

import aws_cdk as cdk
from auto_adjudication.auto_adjudication_stack import AutoAdjudicationStack

app = cdk.App()
AutoAdjudicationStack(app, "AutoAdjudicationStack",
    # Uncomment the next line to use your default AWS account/region
    env=cdk.Environment(account=os.getenv('CDK_DEFAULT_ACCOUNT'), region=os.getenv('CDK_DEFAULT_REGION')),
)

app.synth()
