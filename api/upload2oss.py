import os
import oss2
from datetime import datetime

class up2oss:
    def __init__(self):
        self.endpoint = ''
        self.oss_AccessKey_ID = ""
        self.oss_AccessKey_Secret = ""
        self.bucket_name = ""
        self.auth = oss2.Auth(self.oss_AccessKey_ID, self.oss_AccessKey_Secret)
        self.bucket = oss2.Bucket(self.auth, self.endpoint, self.bucket_name)

    def upload_file(self, local_file_path):
        now = datetime.now()
        date_str = now.strftime('%Y-%m-%d')
        oss_folder_path = 'story_output/' + date_str + '/'
        if not self.bucket.object_exists(oss_folder_path):
            self.bucket.put_object(oss_folder_path, "")

        file_name = "v1_" + date_str + "_" + os.path.basename(local_file_path)
        oss_file_path = os.path.join(oss_folder_path, file_name).replace("\\", "/")

        with open(local_file_path, 'rb') as file_obj:
            self.bucket.put_object(oss_file_path, file_obj, headers={'Content-Disposition': 'inline'})
        return f"https://{self.bucket_name}.oss-cn-beijing.aliyuncs.com/{oss_folder_path}{file_name}"
    
