import pandas as pd
import numpy as np
import os
from tqdm import tqdm
from os.path import dirname, join, abspath

#--------------- Initializing Parameters ----------#

path = dirname(abspath(__file__))

#--------------- Concatenate Yearly Files ------------#

def concat_crash_files(path, final_file_name):
    '''
    Concatenates multiple crash files into a single file.

    Args:
        path (str): Path to the directory containing crash files.
        final_file_name (str): Name of the final file to be created.
    '''

    count = 0
    file_list = os.listdir(path)
    file_list = [x for x in file_list if (x.endswith(".csv") or x.endswith(".CSV"))]

    for file_name in file_list:
        print(file_name)
        df = pd.read_csv(path + file_name, low_memory=False)

        # Convert column names to lowercase
        df.columns = df.columns.str.lower()

        # Print columns to ensure correct column names
        # print(df.columns)

        # Check and process CRASH_DATE
        if 'crash_date' in df.columns:  # handle date in Person_Level_Crash
            df['accident_date'] = pd.to_datetime(df['crash_date'], errors='coerce')
            df['accident_date'] = df['accident_date'].dt.strftime('%Y-%m-%d')

        elif "crash_datetime" in df.columns:  # handle date in Vehicle_Level_Crash
            df['accident_date'] = pd.to_datetime(df['crash_datetime'], errors='coerce')
            df['accident_date'] = df['accident_date'].dt.strftime('%Y-%m-%d')

        # Check for valid LAT and LON values (remove rows with NaN values in LAT/LON)
        if 'lat' in df.columns and 'lon' in df.columns:
            df = df.dropna(subset=['lat', 'lon'])

        # Filter out rows with missing lat or lon values
        df = df[(df['lat'].notna()) & (df['lon'].notna())]

        # Remove rows with latitude or longitude of 0
        df = df[(df["lat"] != 0) & (df["lon"] != 0)]

        # Add "acc_count" column and group by "accident_date", "lat", "lon"
        df["acc_count"] = 1
        df = df.groupby(["accident_date", "lat", "lon"], as_index=False)["acc_count"].sum()

        # Drop duplicate rows and rows with missing values
        df = df.dropna().drop_duplicates().reset_index(drop=True)

        if count > 0:
            # Concatenate with the previous dataframe
            prev_df = pd.read_pickle(final_file_name)
            df = pd.concat([df, prev_df], axis=0, ignore_index=True)

        count += 1

        # Remove duplicate rows again
        df = df.drop_duplicates().reset_index(drop=True)

        # Save the dataframe as a pickle file
        df.to_pickle(final_file_name, protocol=5)

for state_name in ["MA"]:
    print(f"\n********* {state_name} ***********")
    concat_crash_files(path + "/Accidents/" + state_name + "/Crashes_Year/", path + "/Accidents/" + state_name + "/" + state_name + "_crash.pkl")