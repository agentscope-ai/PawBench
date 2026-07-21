import matplotlib.pyplot as plt

# Sample data for the two lines with markers
sft_data_ratios = ['1:8', '1:4', '1:2', '1:1', '2:1']
groundingme_without_rej = [32.8, 27.9, 25.0, 22.5, 20.0]
rejection_category = [30.0, 25.0, 22.0, 20.0, 18.0]

# Create the figure and axis
fig, ax = plt.subplots()

# Plot the two lines with markers
ax.plot(sft_data_ratios, groundingme_without_rej, marker='o', label='GroundingME w/o Rej.')
ax.plot(sft_data_ratios, rejection_category, marker='s', label='Rejection Category')

# Add horizontal dashed baselines
ax.axhline(y=38.8, color='r', linestyle='--', label='Baseline 1 (y=38.8)')
ax.axhline(y=0, color='k', linestyle='--', label='Baseline 2 (y=0)')

# Annotate each data point with its value
for i, txt in enumerate(groundingme_without_rej):
    ax.annotate(f'{txt:.1f}', (sft_data_ratios[i], groundingme_without_rej[i]), textcoords="offset points", xytext=(0,10), ha='center')
for i, txt in enumerate(rejection_category):
    ax.annotate(f'{txt:.1f}', (sft_data_ratios[i], rejection_category[i]), textcoords="offset points", xytext=(0,10), ha='center')

# Set labels and legend
ax.set_xlabel('SFT Data Ratio (Negative to Positive)')
ax.set_ylabel('ACC@0.5')
ax.legend()

# Save the figure
plt.savefig('output/figure6_reproduce.png')

# Show the plot
plt.show()