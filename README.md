# solardata

![GitHub License](https://img.shields.io/github/license/aisvn-data/solardata)
![GitHub Release](https://img.shields.io/github/v/release/aisvn-data/solardata)
![GitHub package.json version (branch)](https://img.shields.io/github/package-json/v/aisvn-data/solardata/main)

Analyze, clean and display collected solar data

## Purpose

I collected a lot of data with several solar stations in Nha Be and Phu My Hung in 2020, and some data in 2021. The data sits mostly in Google Sheets. This repository has three goals:

- Convert the raw data into structured data, maybe CSV
- Analyse and structure the data, clean up, label - maybe sqlite
- Visualize the data on a website, make it searchable

## Convert, analyse and structure - the backend

This should be done in python. It might involve some workers triggered with GitHub Action.

## Display the data - frontend

The data will run with react as frontend, create by vite

## Related repositories

- [aisvn-data/solarpower](https://github.com/aisvn-data/solarpower) Some tinkering and documenting of early steps in May 2020
- [kreier/solarmeter](https://github.com/kreier/solarmeter) Software repository for the 4 collectors of data 2020-2021
- [hviovn/solarmeter](https://github.com/hviovn/solarmeter) New updated solarmeter without the IFTTT service, but using a Cloudflare worker collect the data and store values every two minutes, and finally commit the data to the repository as a pull request to have historic data.
- [kreier/solar](https://github.com/kreier/solar) Endpoint for different measuring stations and point to visualize historic solar data back to 2020, and temperature data back to 2015 in Hofkoh
