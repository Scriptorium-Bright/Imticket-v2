package org.example.ticket.venue.service;

import lombok.RequiredArgsConstructor;
import org.example.ticket.venue.domain.*;
import org.example.ticket.venue.dto.CreateVenueHallRequest;
import org.example.ticket.venue.repository.*;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.List;

@Service
@RequiredArgsConstructor
public class VenueService {
    private final VenueHallRepository venueHallRepository;
    private final VenueHallSeatTemplateRepository templateRepository;

    @Transactional
    public Long createHall(CreateVenueHallRequest request) {
        VenueHall hall = venueHallRepository.save(VenueHall.builder().name(request.name()).build());
        List<VenueHallSeatTemplate> templates = new ArrayList<>(request.rows() * request.seatsPerRow());
        for (int row = 1; row <= request.rows(); row++) {
            for (int number = 1; number <= request.seatsPerRow(); number++) {
                templates.add(VenueHallSeatTemplate.builder()
                        .venueHall(hall).section(request.section()).rowNumber(row).seatNumber(number)
                        .grade(request.grade()).build());
            }
        }
        templateRepository.saveAll(templates);
        return hall.getId();
    }
}
