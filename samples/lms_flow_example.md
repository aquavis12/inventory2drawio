# LMS study site: request flow

1. Students open lms.example.com, served by CloudFront from the site bucket.
2. The site sends activity and quiz results to vr-activity-api.
3. vr-activity-api checks the student's login with the Cognito user pool.
4. vr-activity-api calls vr-activity-fn.
5. vr-activity-fn stores activity in vr-activity, quiz answers in vr-quiz and submissions in vr-submissions.
6. Every evening, vr-activity-fn publishes a summary to vr-daily-submissions.
7. vr-daily-submissions emails the instructor.
