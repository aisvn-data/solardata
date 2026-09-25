# solardata

![GitHub License](https://img.shields.io/github/license/aisvn-data/solardata)
![GitHub Release](https://img.shields.io/github/v/release/aisvn-data/solardata)
![GitHub package.json version (branch)](https://img.shields.io/github/package-json/v/aisvn-data/solardata/main)

Analyze, clean and display collected solar data.

## Website

The repository now includes the initial **v0.1.0** Vite + React website outline. It provides a lightweight landing page for the project, station overview cards, and a small roadmap for future data work.

### Run locally

```bash
npm install
npm run dev
```

Build a production bundle with `npm run build`. The Vite base path is configured for GitHub Pages at `/solardata/`.

## Purpose

I collected a lot of data with several solar stations in Nha Be and Phu My Hung in 2020, and some data in 2021. The data sits mostly in Google Sheets. This repository has three goals:

- Convert the raw data into structured data, maybe CSV
- Analyse and structure the data, clean up, label - maybe sqlite
- Visualize the data on a website, make it searchable

## Data sources

Most data was forwared with the service [IFTTT.com](https://ifttt.com/explore) that was free in 2020 and could easily have 5 different services available over webhooks. In time it was reduced to three, and then even this service was put behind a Pro subscription. But the data is in the Google Sheets - now lets extract it. We have

- IFTTT_test 0-18 2020-07-08 - 2020-09-26
- Voltage_phumy 0-2 2020-07-10 - 20220-07-13
- IFTTT_AISVN_Solar 0-6 2020-06-13
- IFTTT_phumy2 0-39 2020-06-18 - 2020-12-21
- IFTTT_phumy2 40-75 2021-02-15 - 2021-11-14
- IFTTT_phumy2 76-101, 0-12 2022-03-06 - 2022-12-18
- IFTTT_phumy2 13-97 2023-01-02 - 2023-11-24
- IFTTT_phymy2 0-2, 98-99 2024-01-14 - 2024-02-02
- IFTTT_aisvn 0-38 2020-06-18 - 2022-02-23
- IFTTT_aisvn2 0-78 2020-06-23 - 2021-11-01

From IFTTT:

- **aisvn** run 94437 times from 2020-09-06 to 2022-02-23
- **solar_reading** un 428698 times from 2020-09-06 to 2024-02-02

Archived older Applets:

- phumy
- test
- aisvn2

## Convert, analyse and structure - the backend

This should be done in python. It might involve some workers triggered with GitHub Action.

## Display the data - frontend

The data will run with react as frontend, create by vite

## Related repositories

- [aisvn-data/solarpower](https://github.com/aisvn-data/solarpower) Some tinkering and documenting of early steps in May 2020
- [kreier/solarmeter](https://github.com/kreier/solarmeter) Software repository for the 4 collectors of data 2020-2021
- [hviovn/solarmeter](https://github.com/hviovn/solarmeter) New updated solarmeter without the IFTTT service, but using a Cloudflare worker collect the data and store values every two minutes, and find historical data
- [kreier/solar](https://github.com/kreier/solar) Endpoint for different measuring stations and point to visualize historic solar data back to 2020, and temperature data back to 2015 in Hofkoh
