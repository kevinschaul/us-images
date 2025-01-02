#!/usr/bin/env node

/* Download Congress member photos using Congress.gov API.
 *
 * Requires an API key in environment variable CONGRESS_API_KEY. Get one at:
 * https://api.congress.gov/sign-up/
 *
 * The images themselves are downloaded with Puppeteer to avoid being
 * blocked by Cloudfront.
 */

import { Command } from "commander";
import fs from "fs/promises";
import path from "path";
import { fileURLToPath } from "url";
import { execSync } from "child_process";
import puppeteer from "puppeteer";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const program = new Command();
program
  .description("Download Congress member photos using the Congress.gov API")
  .option("-n, --congress <congress>", "Congress session number", "118")
  .option(
    "-o, --outdir <path>",
    "Directory to save photos in",
    "congress/original",
  )
  .option("-d, --delay <seconds>", "Rate-limiting delay between requests", "5")
  .option("-t, --test", "Test mode: don't actually save images", false);

program.parse(process.argv);
const options = program.opts();

async function pause(last, delay) {
  if (!last) return new Date();

  const now = new Date();
  const delta = (now - last) / 1000;

  if (delta < delay) {
    const sleep = delay - delta;
    console.log(`Sleep for ${Math.round(sleep)} seconds`);
    await new Promise((resolve) => setTimeout(resolve, sleep * 1000));
  }
  return new Date();
}

async function getMemberList(congressNumber, apiKey) {
  const url = `https://api.congress.gov/v3/member/congress/${congressNumber}`;
  const currentMember = congressNumber === "118" ? "True" : "False";

  const params = new URLSearchParams({
    currentMember,
    format: "json",
    limit: "250",
    api_key: apiKey,
  });

  const allMembers = [];
  let offset = 0;

  while (true) {
    params.set("offset", offset.toString());
    const response = await fetch(`${url}?${params}`);

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    const data = await response.json();
    const members = data.members;
    allMembers.push(...members);

    if (members.length < parseInt(params.get("limit"))) break;

    offset += parseInt(params.get("limit"));
    console.log(`Fetched ${members.length} members, getting next page...`);
  }

  console.log(`Retrieved ${allMembers.length} total members`);
  return allMembers;
}

async function saveMetadata(bioguideId, attribution) {
  const outdir = "congress/metadata";
  await fs.mkdir(outdir, { recursive: true });
  await fs.writeFile(
    path.join(outdir, `${bioguideId}.yaml`),
    `name: Congress.gov API\nattribution: ${attribution}\nlink: https://www.congress.gov\n`,
  );
}

async function downloadPhotos(browser, members, outdir, delay, test) {
  await fs.mkdir(outdir, { recursive: true });
  const page = await browser.newPage();

  let currentImageBuffer = null;

  page.on("response", async (response) => {
    if (response.request().resourceType() === "document") {
      currentImageBuffer = await response.buffer();
    }
  });

  let lastRequestTime = null;
  let ok = 0;

  for (const member of members) {
    const bioguideId = member.bioguideId.toUpperCase();

    if (!member.depiction?.imageUrl) {
      console.log(`No image available for ${bioguideId}`);
      continue;
    }

    const photoUrl = member.depiction.imageUrl.replace("_200.jpg", ".jpg");
    const attribution = member.depiction.attribution || "Unknown";
    const filename = path.join(outdir, `${bioguideId}.jpg`);

    try {
      const exists = await fs
        .access(filename)
        .then(() => true)
        .catch(() => false);
      if (exists) {
        console.log("Image already exists:", filename);
        continue;
      }

      if (!test) {
        lastRequestTime = await pause(lastRequestTime, delay);

        console.log("Downloading image:", photoUrl);
        currentImageBuffer = null;
        await page.goto(photoUrl, { waitUntil: "networkidle0" });

        if (currentImageBuffer) {
          await fs.writeFile(filename, currentImageBuffer);
          await saveMetadata(bioguideId, attribution);
          console.log("Saved image:", filename);
          ok++;
        }
      }
    } catch (error) {
      console.error("Image not available:", error.message);
    }
  }

  await page.close();
  console.log("Downloaded", ok, "member photos.");
  return ok;
}

function resizePhotos() {
  try {
    execSync(path.join(__dirname, "resize-photos.sh"));
  } catch (error) {
    console.error("Error resizing photos:", error.message);
  }
}

async function main() {
  const apiKey = process.env.CONGRESS_API_KEY;
  if (!apiKey) {
    throw new Error("CONGRESS_API_KEY environment variable not set");
  }

  const browser = await puppeteer.launch({ headless: false });
  try {
    const members = await getMemberList(options.congress, apiKey);
    const number = await downloadPhotos(
      browser,
      members,
      options.outdir,
      parseInt(options.delay),
      options.test,
    );

    if (number > 0) {
      resizePhotos();
    }
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error("Error:", error);
  process.exit(1);
});
